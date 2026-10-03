"""Morning next-best-action queue: one decision per open case, in the gold.nba_decisions shape.

    python -m trustissues.nba.queue                # writes warehouse/p3/nba_decisions.parquet
    python -m trustissues.nba.queue --write-gold   # also replaces this as-of date's rows in gold.nba_decisions

For each open case on AS_OF_DATE:
1. rules (nba/rules.py): which actions are allowed, and the rule that blocked each other one
2. risk: the break-risk model on the case's open promise, scored as of when it was made (how the model
   was trained); NULL when the case has no open promise
3. value (nba/actions.py): expected extra payment minus contact cost for every allowed action
4. hardship (nba/hardship.py): never harsher than without the signal, route to a certified agent
5. card (nba/explain.py): top reasons, why the action beat the runner-up, blocked actions, summary
6. approval: vulnerability, hardship, high balance, or the daily digital-reminder batch

--write-gold needs the warehouse write lock, so run it as a stage after the Layer 1 pipeline, never
alongside it. golden_customer_id holds crm_customer_id until identity resolution publishes golden IDs.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, time

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.common.interfaces import BlockedAction, DecisionCard, Reason
from trustissues.features import compute, sources
from trustissues.nba import actions, explain, hardship, rules

DECISIONS_FILE = "nba_decisions.parquet"
COLUMNS = ["recommendation_id", "golden_customer_id", "case_id", "agent_id", "as_of_date", "action", "channel",
           "risk_score", "expected_value", "requires_approval", "approval_reason", "decision_card", "model_version",
           "feature_snapshot_id", "created_ts"]
DIGITAL = ("sms", "email", "push")
AGENT_ACTIONS = ("agent_call", "wait")  # stay in the assigned agent's queue; the rest go to batches


@dataclass
class CaseInput:
    case_id: str
    customer_id: str
    assigned_agent_id: str | None
    agent_certified: bool
    bucket: str | None
    amount: float  # open promise amount, else total overdue
    flags: rules.CaseFlags
    signal: hardship.HardshipSignal
    risk: float | None = None  # break risk of the open promise
    reasons: list[Reason] = field(default_factory=list)


@dataclass
class Context:
    as_of: object
    rates: dict
    costs: dict
    high_balance: float
    model_version: str
    snapshot_id: str | None


def approval_reason(c: CaseInput, action: str, high_balance: float) -> str | None:
    """First mandatory-approval reason, in priority order, or None."""
    for reason, applies in (("vulnerability", c.signal.vulnerable),
                            ("hardship", c.signal.level != "none"),
                            ("high_balance", c.amount >= high_balance),
                            ("digital_batch", actions.CATALOGUE[action].channel in DIGITAL)):
        if applies:
            return reason
    return None


def decide_case(c: CaseInput, ctx: Context) -> dict:
    allowed = rules.allowed_actions(c.flags)
    # without an open promise, the chance of not paying anyway stands in for break risk
    risk_for_value = c.risk if c.risk is not None else 1 - ctx.rates.get(c.bucket or "", {}).get("wait", 0.0)
    values = actions.value_actions(allowed.allowed, c.bucket, risk_for_value, c.amount, ctx.rates, ctx.costs)
    d = hardship.decide(c.flags, values, c.signal)
    candidates = values
    if c.signal.active:
        cap = min(hardship.MAX_HARSHNESS_IN_HARDSHIP, actions.harshness(d.baseline_action))
        candidates = {a: v for a, v in values.items() if actions.harshness(a) <= cap}
    runner = explain.runner_up(d.action, candidates)

    reason = approval_reason(c, d.action, ctx.high_balance)

    agent = c.assigned_agent_id if d.action in AGENT_ACTIONS else None
    if d.route_to_certified_agent and not c.agent_certified:
        agent = None  # to be assigned to a certified agent
    hard_stop = next((r for r in allowed.blocked.values() if r in rules.HARD_STOPS), None)

    card = DecisionCard(
        recommended_action=d.action,
        channel=actions.CATALOGUE[d.action].channel,
        risk_score=None if c.risk is None else round(c.risk, 4),
        top_reasons=c.reasons,
        runner_up_action=runner,
        why_beat_runner_up=explain.why_beat(d.action, runner, candidates),
        blocked_actions=[BlockedAction(a, r) for a, r in allowed.blocked.items()],
        evidence=[],  # P4 text features (gold.text_features) fill this once they exist
        requires_approval=reason is not None,
        approval_reason=reason,
        summary=explain.summary(d.action, c.risk, c.reasons, d.route_to_certified_agent, hard_stop),
    )
    return {
        "recommendation_id": f"REC-{ctx.as_of:%Y%m%d}-{c.case_id}",
        "golden_customer_id": c.customer_id,
        "case_id": c.case_id,
        "agent_id": agent,
        "as_of_date": ctx.as_of,
        "action": d.action,
        "channel": card.channel,
        "risk_score": card.risk_score,
        "expected_value": round(values.get(d.action, 0.0), 2),
        "requires_approval": card.requires_approval,
        "approval_reason": reason,
        "decision_card": card.to_json(),
        "model_version": ctx.model_version,
        "feature_snapshot_id": ctx.snapshot_id if c.risk is not None else None,
        "created_ts": datetime.combine(ctx.as_of, time(6, 0)),
    }


def score_open_promises(con, model, snapshot_dir) -> tuple[dict, dict, str]:
    """ptp_id -> calibrated risk and top reasons, for open promises that are a case's current promise."""
    compute.register_spine(con, """
        SELECT p.ptp_id, p.ptp_created_ts AS event_ts FROM src_promises p
        WHERE p.ptp_status = 'open'
          AND p.ptp_id IN (SELECT current_ptp_id FROM src_cases WHERE case_status = 'open')""")
    cols = compute.compute(con)
    df = con.sql(compute.joined_sql(cols)).df().set_index("ptp_id").sort_index()
    risk = model.predict(df)
    contrib = model.contributions(df)
    snap = df.assign(risk_score=risk)
    # one snapshot per as-of date and model: a rerun replaces it, like the gold rows
    snapshot_id = f"FS-{config.AS_OF_DATE:%Y%m%d}-{model.name}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snap.reset_index().to_parquet(snapshot_dir / f"{snapshot_id}.parquet", index=False)
    values = df.to_dict("index")
    reasons = {p: explain.top_reasons(values[p], row) for p, row in contrib.to_dict("index").items()}
    return dict(zip(df.index, risk.tolist())), reasons, snapshot_id


def load_cases(con) -> list[dict]:
    rel = con.sql("""
        SELECT c.case_id, c.crm_customer_id, c.assigned_agent_id, c.current_bucket, c.total_overdue,
               c.current_ptp_id, p.ptp_amount,
               coalesce(c.hardship_flag, false) AS hardship_flag,
               coalesce(c.vulnerable_customer_flag, false) OR coalesce(f.vulnerability_flag, false) AS vulnerable,
               coalesce(a.vulnerable_customer_certified, false) AND a.status = 'active' AS agent_certified
        FROM src_cases c
        LEFT JOIN src_crm_safety_flags f USING (crm_customer_id)
        LEFT JOIN src_agents a ON a.agent_id = c.assigned_agent_id
        LEFT JOIN src_promises p ON p.ptp_id = c.current_ptp_id AND p.ptp_status = 'open'
        WHERE c.case_status = 'open'
        ORDER BY c.case_id""")
    return [dict(zip(rel.columns, r)) for r in rel.fetchall()]


def build(con) -> tuple[list[dict], dict]:
    from trustissues.nba.model import BreakModel

    sources.register(con)
    model = BreakModel()
    risk, reasons, snapshot_id = score_open_promises(con, model, config.p3_dir() / "snapshots")
    cases = load_cases(con)
    flags = rules.load_case_flags(con, [c["case_id"] for c in cases])
    ctx = Context(as_of=config.AS_OF_DATE, rates=actions.load_payment_rates(), costs=actions.load_unit_costs(con),
                  high_balance=float(config.settings()["p3"]["high_balance_cad"]),
                  model_version=model.name, snapshot_id=snapshot_id)
    rows = []
    for c in cases:
        ptp = c["current_ptp_id"] if c["current_ptp_id"] in risk else None
        rows.append(decide_case(CaseInput(
            case_id=c["case_id"], customer_id=c["crm_customer_id"], assigned_agent_id=c["assigned_agent_id"],
            agent_certified=bool(c["agent_certified"]), bucket=c["current_bucket"],
            amount=float(c["ptp_amount"] if ptp and c["ptp_amount"] is not None else c["total_overdue"] or 0.0),
            flags=flags[c["case_id"]],
            signal=hardship.HardshipSignal("possible" if c["hardship_flag"] else "none", bool(c["vulnerable"])),
            risk=risk.get(ptp) if ptp else None, reasons=reasons.get(ptp, []) if ptp else [],
        ), ctx))
    return rows, {"snapshot_id": snapshot_id, "scored_promises": len(risk)}


def write_gold(rows_path, as_of) -> int:
    """Replace this as-of date's rows in gold.nba_decisions. Needs the write lock."""
    from trustissues.common.interfaces import apply_stubs

    con = connect()
    apply_stubs(con)
    con.execute("BEGIN")
    con.execute("DELETE FROM gold.nba_decisions WHERE as_of_date = ?", [as_of])
    con.execute(f"INSERT INTO gold.nba_decisions SELECT {', '.join(COLUMNS)} FROM read_parquet('{rows_path}')")
    con.execute("COMMIT")
    return con.execute("SELECT count(*) FROM gold.nba_decisions WHERE as_of_date = ?", [as_of]).fetchone()[0]


def write_report(path, n: int, info: dict, example: dict) -> None:
    import duckdb

    from trustissues.nba.evaluate import write_section

    q = duckdb.connect()
    f = f"read_parquet('{path.as_posix()}')"
    by_action = q.execute(f"""
        SELECT action, count(*), count(risk_score), avg(risk_score), avg(expected_value),
               count(*) FILTER (WHERE requires_approval)
        FROM {f} GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    by_reason = q.execute(f"SELECT coalesce(approval_reason, 'none'), count(*) FROM {f} GROUP BY 1 ORDER BY 2 DESC").fetchall()
    rules_hit = q.execute(f"""
        SELECT r, count(DISTINCT case_id) FROM (
            SELECT case_id, unnest(regexp_extract_all(decision_card, '"rule": "([a-z_0-9]+)"', 1)) AS r FROM {f})
        GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    t1 = "\n".join(f"| `{a}` | {c:,} | {p:,} | {'' if r is None else f'{r:.2f}'} | {v:,.2f} | {ap:,} |"
                   for a, c, p, r, v, ap in by_action)
    t2 = "\n".join(f"| {r} | {c:,} |" for r, c in by_reason)
    t3 = "\n".join(f"| `{r}` | {c:,} |" for r, c in rules_hit)
    write_section("nba_queue", f"""
## Morning NBA queue (`nba/queue.py`)

{n:,} open cases on {config.AS_OF_DATE}, one decision each, in the `gold.nba_decisions` shape
(`warehouse/p3/nba_decisions.parquet`). {info['scored_promises']:,} cases have an open promise, scored with
the break-risk model (feature snapshot `{info['snapshot_id']}`); the rest have no risk score. Action value =
risk x (historical 30-day payment rate after the action - rate when waiting) x amount at stake - unit cost.
The rates are observational, so the values rank actions; they do not prove uplift.

| Action | Cases | With open promise | Avg risk | Avg expected value (CAD) | Need approval |
|---|---|---|---|---|---|
{t1}

| Approval reason | Cases |
|---|---|
{t2}

Cases where a rule removed at least one action:

| Rule | Cases |
|---|---|
{t3}

Example decision card:

```json
{json.dumps(example, indent=2)}
```
""")


def main() -> int:
    import duckdb
    import pandas as pd

    ap = argparse.ArgumentParser(description="Build the morning NBA queue")
    ap.add_argument("--write-gold", action="store_true", help="also write gold.nba_decisions (needs the write lock)")
    args = ap.parse_args()

    con = connect(read_only=True)
    rows, info = build(con)
    con.close()
    out = config.p3_dir() / DECISIONS_FILE
    pd.DataFrame(rows, columns=COLUMNS).to_parquet(out, index=False)
    print(f"wrote {len(rows):,} decisions to {out} (scored {info['scored_promises']:,} open promises, "
          f"feature snapshot {info['snapshot_id']})")
    summary = duckdb.connect().sql(f"""
        SELECT action, count(*) AS cases, count(risk_score) AS with_promise, round(avg(risk_score), 3) AS avg_risk,
               round(avg(expected_value), 2) AS avg_value, count(*) FILTER (WHERE requires_approval) AS need_approval
        FROM read_parquet('{out.as_posix()}') GROUP BY 1 ORDER BY 2 DESC""")
    print(summary)
    print(duckdb.connect().sql(f"SELECT approval_reason, count(*) FROM read_parquet('{out.as_posix()}') GROUP BY 1 ORDER BY 2 DESC"))
    example = next((r for r in rows if r["risk_score"] is not None and r["action"] != "wait"), rows[0])
    print("example decision card:\n" + json.dumps(json.loads(example["decision_card"]), indent=2))
    write_report(out, len(rows), info, json.loads(example["decision_card"]))
    if args.write_gold:
        n = write_gold(out.as_posix(), config.AS_OF_DATE)
        print(f"gold.nba_decisions now has {n:,} rows for {config.AS_OF_DATE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
