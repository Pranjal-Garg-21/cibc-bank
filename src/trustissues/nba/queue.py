"""Morning next-best-action queue: one decision per open case, in the gold.nba_decisions shape.

    python -m trustissues.nba.queue                # writes warehouse/p3/nba_decisions.parquet
    python -m trustissues.nba.queue --write-gold   # also replaces this as-of date's rows in gold.nba_decisions

For each open case on AS_OF_DATE:
1. rules (nba/rules.py): which actions are allowed, and the rule that blocked each other one
2. risk: the case model's chance the case is NOT paid up within 30 days (every open case), from data before
   the as-of date. Cases with an open promise also get the promise model's break risk on the card.
3. value (nba/actions.py): the randomised test's effect per contact x the case's risk x its reach on the
   channel x amount overdue, minus the channel's unit cost
4. hardship (nba/hardship.py): a hardship or vulnerable case gets no payment reminders or collections calls,
   only `wait` or a `support_call` by a certified agent when one is due (no programme offered yet, plan ending,
   follow-up, or a vulnerable customer who would otherwise be contacted), with its eligible programmes
5. capacity (nba/allocate.py): support calls first, then the most valuable cases, into the day's channel and
   agent capacity; an action that does not fit falls back to the case's next best. Every hardship or
   vulnerable case gets a certified owner (agent_id) even when it waits
6. card (nba/explain.py) and approval, only when something is done (never for `wait`): vulnerability,
   hardship, high balance, or the daily digital batch

risk_score holds the case model's risk. --write-gold needs the warehouse write lock, so run it as a stage
after the Layer 1 pipeline, never alongside it. golden_customer_id is the case's person from gold.case_customer
(P1's identity resolution); the 9 open cases it could not tie to anyone keep their CRM ID there.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, time

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.common.interfaces import BlockedAction, DecisionCard, HardshipOption, Reason
from trustissues.features import compute, sources
from trustissues.nba import actions, allocate, explain, hardship, rules

DECISIONS_FILE = "nba_decisions.parquet"
COLUMNS = ["recommendation_id", "golden_customer_id", "case_id", "agent_id", "as_of_date", "action", "channel",
           "risk_score", "expected_value", "requires_approval", "approval_reason", "decision_card", "model_version",
           "feature_snapshot_id", "created_ts"]
DIGITAL = ("sms", "email", "push")
AGENT_ACTIONS = ("agent_call", "support_call", "wait")  # stay with an agent; the rest go to batches


@dataclass
class CaseInput:
    case_id: str
    customer_id: str
    assigned_agent_id: str | None
    agent_certified: bool
    amount: float  # total overdue
    flags: rules.CaseFlags
    signal: hardship.HardshipSignal
    risk: float  # case model: chance of NOT being paid up within 30 days
    reasons: list[Reason] = field(default_factory=list)
    reach: dict[str, float] = field(default_factory=dict)
    promise_risk: float | None = None  # promise model, when the case has an open promise
    team: str | None = None
    history: hardship.HardshipHistory = field(default_factory=hardship.HardshipHistory)
    options: list[HardshipOption] = field(default_factory=list)  # eligible hardship programmes


@dataclass
class Context:
    as_of: object
    costs: dict
    effect: float  # extra cure chance per contact for an average case (randomised test)
    base_risk: float  # average not-cured rate in that test
    high_balance: float
    model_version: str
    snapshot_id: str | None


@dataclass
class Plan:
    case: CaseInput
    allowed: rules.RuleResult
    candidates: dict[str, float]  # what may be chosen (after hardship), with values
    order: list[str]  # candidates worth more than waiting, best first, then wait
    route_to_certified: bool
    support_reason: str | None = None  # why a support call is due (nba/hardship.py)
    dropped: list[str] = field(default_factory=list)  # contacts given up: the person is contacted on another case
    contacted_on: str | None = None  # that other case


SAME_DAY = "customer_already_contacted_today"


def one_contact_per_customer(plans: list[Plan]) -> int:
    """One contact per person per day. Of a person's cases that would contact them, the one with a support call
    (hardship and vulnerability come first), else the most valuable contact, keeps it; the others wait, their
    contacts blocked as customer_already_contacted_today. Runs before capacity is allocated, so what they would
    have used goes to other customers. Returns the number of cases turned into waits."""
    def rank(p: Plan):
        return p.order[0] == hardship.SUPPORT, p.candidates.get(p.order[0], 0.0), p.case.case_id

    keep: dict[str, Plan] = {}
    for p in plans:
        if p.order[0] != "wait" and (p.case.customer_id not in keep or rank(p) > rank(keep[p.case.customer_id])):
            keep[p.case.customer_id] = p
    n = 0
    for p in plans:
        k = keep.get(p.case.customer_id)
        if k is None or k is p or p.order[0] == "wait":
            continue
        p.dropped, p.order, p.contacted_on = [a for a in p.order if a != "wait"], ["wait"], k.case.case_id
        n += 1
    return n


def approval_reason(c: CaseInput, action: str, high_balance: float) -> str | None:
    """First mandatory-approval reason, in priority order, or None. Waiting needs no approval."""
    if action == "wait":
        return None
    for reason, applies in (("vulnerability", c.signal.vulnerable),
                            ("hardship", c.signal.level != "none"),
                            ("high_balance", c.amount >= high_balance),
                            ("digital_batch", actions.CATALOGUE[action].channel in DIGITAL)):
        if applies:
            return reason
    return None


def plan_case(c: CaseInput, ctx: Context) -> Plan:
    allowed = rules.allowed_actions(c.flags)
    values = actions.value_actions(allowed.allowed, c.risk, c.amount, ctx.costs, ctx.effect, ctx.base_risk, c.reach)
    d = hardship.decide(c.flags, values, c.signal, c.history, ctx.as_of)
    if c.signal.active:  # wait, or a support call when one is due and allowed; never a reminder
        candidates = {"wait": 0.0, **({hardship.SUPPORT: 0.0} if d.action == hardship.SUPPORT else {})}
        order = [a for a in (hardship.SUPPORT, "wait") if a in candidates]
        return Plan(c, allowed, candidates, order, True, d.support_reason)
    candidates = values
    better = [a for a in candidates if a != "wait" and candidates[a] > 0]
    order = sorted(better, key=lambda a: (-candidates[a], actions.harshness(a))) + ["wait"]
    return Plan(c, allowed, candidates, order, False)


def default_agent(plan: Plan, action: str) -> str | None:
    c = plan.case
    if plan.route_to_certified:  # a certified owner; assign_owners finds one when the case's agent is not
        return c.assigned_agent_id if c.agent_certified else None
    if action not in AGENT_ACTIONS:
        return None  # batches have no agent
    return c.assigned_agent_id


def request(p: Plan) -> allocate.Request:
    """The allocation request for a plan. Support calls go first (tier 0), most exposed first (risk x overdue)."""
    c = p.case
    if p.order[0] == hardship.SUPPORT:
        return allocate.Request(p.order, c.risk * c.amount, c.assigned_agent_id, c.team, True, tier=0)
    return allocate.Request(p.order, p.candidates[p.order[0]], c.assigned_agent_id, c.team, p.route_to_certified)


def card_channel_is_real_time(action: str) -> bool:
    return actions.CATALOGUE[action].channel in rules.REAL_TIME


def render(plan: Plan, assignment: allocate.Assignment, ctx: Context) -> dict:
    c, action = plan.case, assignment.action
    runner = explain.runner_up(action, plan.candidates)
    reason = approval_reason(c, action, ctx.high_balance)
    hard_stop = next((r for r in plan.allowed.blocked.values() if r in rules.HARD_STOPS), None)
    blocked = [BlockedAction(a, r) for a, r in plan.allowed.blocked.items()
               if plan.route_to_certified or a != hardship.SUPPORT]  # support calls exist only for flagged cases
    blocked += [BlockedAction(a, "no_capacity") for a in assignment.no_capacity]
    blocked += [BlockedAction(a, SAME_DAY) for a in plan.dropped]
    window = rules.contact_window(c.flags) if card_channel_is_real_time(action) else None
    window_text = f"{window[0]:%H:%M}–{window[1]:%H:%M} customer-local" if window else None
    support = None
    if plan.route_to_certified:
        support = explain.support_text(action, plan.support_reason,
                                       hardship.waiting_reason(c.signal, c.history, ctx.as_of),
                                       plan.allowed.blocked.get(hardship.SUPPORT)
                                       or (SAME_DAY if hardship.SUPPORT in plan.dropped else None),
                                       assignment.no_capacity)
    card = DecisionCard(
        recommended_action=action,
        channel=actions.CATALOGUE[action].channel,
        risk_score=round(c.risk, 4),
        top_reasons=c.reasons,
        runner_up_action=runner,
        why_beat_runner_up=explain.why_beat(action, runner, plan.candidates, assignment.no_capacity, support,
                                            same_day=plan.contacted_on),
        blocked_actions=blocked,
        evidence=[],  # P4 text features (gold.text_features) fill this once they exist
        requires_approval=reason is not None,
        approval_reason=reason,
        summary=explain.summary(action, c.risk, c.reasons, plan.route_to_certified, hard_stop,
                                promise_risk=c.promise_risk, no_capacity=assignment.no_capacity, support=support,
                                options=[o.name for o in c.options], window=window_text,
                                same_day=plan.contacted_on),
        support_reason=plan.support_reason if action == hardship.SUPPORT else None,
        hardship_options=c.options,
        contact_window=window_text,
    )
    return {
        "recommendation_id": f"REC-{ctx.as_of:%Y%m%d}-{c.case_id}",
        "golden_customer_id": c.customer_id,
        "case_id": c.case_id,
        "agent_id": assignment.agent_id,
        "as_of_date": ctx.as_of,
        "action": action,
        "channel": card.channel,
        "risk_score": card.risk_score,
        "expected_value": round(plan.candidates.get(action, 0.0), 2),
        "requires_approval": card.requires_approval,
        "approval_reason": reason,
        "decision_card": card.to_json(),
        "model_version": ctx.model_version,
        "feature_snapshot_id": ctx.snapshot_id,
        "created_ts": datetime.combine(ctx.as_of, time(6, 0)),
    }


def decide_case(c: CaseInput, ctx: Context) -> dict:
    """One case with unlimited capacity (used by tests and for a single case)."""
    plan = plan_case(c, ctx)
    best = plan.order[0]
    return render(plan, allocate.Assignment(best, default_agent(plan, best), []), ctx)


def score_cases(con, snapshot_dir) -> tuple[dict, dict, dict, str]:
    """case_id -> case risk, top reasons and reach, for every open case, from data before the as-of date."""
    from trustissues.nba.model import BreakModel
    from trustissues.nba.train import CASE_MODEL_NAME

    as_of = config.AS_OF_DATE
    compute.register_case_spine(con, f"""
        SELECT case_id || '@{as_of}' AS row_id, case_id, TIMESTAMP '{as_of}' AS event_ts
        FROM src_cases WHERE case_status = 'open'""")
    cols = compute.compute(con, compute.CASE_GROUPS)
    df = con.sql(compute.joined_sql(cols, ", s.case_id")).df().set_index("case_id").sort_index()
    model = BreakModel(CASE_MODEL_NAME)
    risk = model.predict(df)
    contrib = model.contributions(df)
    snapshot_id = f"FS-{as_of:%Y%m%d}-{model.name}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    df.assign(risk_score=risk).reset_index().to_parquet(snapshot_dir / f"{snapshot_id}.parquet", index=False)
    values = df.to_dict("index")
    averages = {f: df[f].mean() for f in actions.REACH_FEATURES.values()}
    reasons = {c: explain.top_reasons(values[c], row) for c, row in contrib.to_dict("index").items()}
    reach = {c: actions.reach(values[c], averages) for c in df.index}
    return dict(zip(df.index, risk.tolist())), reasons, reach, snapshot_id


def score_open_promises(con, snapshot_dir=None) -> dict[str, float]:
    """ptp_id -> break risk, for open promises that are an open case's current promise. With snapshot_dir, the
    features and scores are saved as FS-<date>-ptp_break_v0.parquet (read by governance/monitoring.py)."""
    from trustissues.nba.model import BreakModel

    compute.register_spine(con, """
        SELECT p.ptp_id, p.ptp_created_ts AS event_ts FROM src_promises p
        WHERE p.ptp_status = 'open'
          AND p.ptp_id IN (SELECT current_ptp_id FROM src_cases WHERE case_status = 'open')""")
    cols = compute.compute(con)
    df = con.sql(compute.joined_sql(cols)).df().set_index("row_id").sort_index()
    model = BreakModel()
    risk = model.predict(df)
    if snapshot_dir is not None:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        df.assign(risk_score=risk).rename_axis("ptp_id").reset_index().to_parquet(
            snapshot_dir / f"FS-{config.AS_OF_DATE:%Y%m%d}-{model.name}.parquet", index=False)
    return dict(zip(df.index, risk.tolist()))


def load_cases(con, as_of) -> list[dict]:
    """Open cases with what routing and hardship need. Offers count only if made before the as-of date, and their
    answer only if given before it (some offers and answers in the data are dated after the snapshot)."""
    rel = con.sql(f"""
        WITH opened AS (
            SELECT * FROM src_card_opened UNION ALL SELECT * FROM src_loan_opened
            UNION ALL SELECT * FROM src_deposit_opened
        ),
        offers AS (
            SELECT case_id, max(offer_ts) AS last_offer_ts,
                   -- offers carry no break date: a plan accepted in the last 90 days that is now broken
                   bool_or(plan_status = 'broken' AND response_ts < TIMESTAMP '{as_of}'
                           AND response_ts >= TIMESTAMP '{as_of}' - INTERVAL 90 DAY) AS broken_plan_90d
            FROM src_hardship_offers WHERE offer_ts < TIMESTAMP '{as_of}' GROUP BY 1
        )
        SELECT c.case_id, c.golden_customer_id, c.crm_customer_id, c.assigned_agent_id, c.assigned_team, c.total_overdue,
               c.current_ptp_id, c.primary_product, coalesce(c.current_dpd, 0) AS current_dpd,
               coalesce(c.hardship_flag, false) AS hardship_flag, c.hardship_source,
               coalesce(c.vulnerable_customer_flag, false) OR coalesce(f.vulnerability_flag, false) AS vulnerable,
               coalesce(a.vulnerable_customer_certified, false) AND a.status = 'active' AS agent_certified,
               CASE WHEN c.hardship_program_accepted THEN c.hardship_plan_end END AS plan_end,
               coalesce(c.hardship_program_offered, false) OR o.last_offer_ts IS NOT NULL AS ever_offered,
               o.last_offer_ts, coalesce(o.broken_plan_90d, false) AS broken_plan_90d,
               date_diff('month', d.opened, DATE '{as_of}') AS months_on_book
        FROM src_cases c
        LEFT JOIN (SELECT golden_customer_id, bool_or(vulnerability_flag) AS vulnerability_flag
                   FROM src_crm_safety_flags GROUP BY 1) f USING (golden_customer_id)  -- the person, any record
        LEFT JOIN src_agents a ON a.agent_id = c.assigned_agent_id
        LEFT JOIN offers o USING (case_id)
        LEFT JOIN opened d ON d.account_id = c.primary_account_id
        WHERE c.case_status = 'open'
        ORDER BY c.case_id""")
    return [dict(zip(rel.columns, r)) for r in rel.fetchall()]


def load_uses_12m(con, as_of) -> dict[str, dict[str, int]]:
    """case_id -> {program_id: plans accepted in the 12 months before the as-of date} (max_uses_12m)."""
    rows = con.execute(f"""
        SELECT case_id, program_id, count(*) FROM src_hardship_offers
        WHERE customer_response = 'accepted' AND response_ts < TIMESTAMP '{as_of}'
          AND response_ts >= TIMESTAMP '{as_of}' - INTERVAL 12 MONTH
        GROUP BY ALL""").fetchall()
    out: dict[str, dict[str, int]] = {}
    for case_id, program, n in rows:
        out.setdefault(case_id, {})[program] = n
    return out


def case_input(c: dict, flags: rules.CaseFlags, programs: list[dict], uses: dict, as_of, **model) -> CaseInput:
    """One open case (a load_cases row) as the planner's input, with its hardship signal, history and options."""
    level = hardship.level_from_case(c["hardship_flag"], c["hardship_source"])
    ctx = hardship.AccountContext(product=c["primary_product"], dpd=c["current_dpd"],
                                  months_on_book=c["months_on_book"] if c["months_on_book"] is not None else 0,
                                  insolvency=flags.insolvency, broken_plan_90d=c["broken_plan_90d"],
                                  uses_12m=uses.get(c["case_id"], {}))
    return CaseInput(
        case_id=c["case_id"], customer_id=c["golden_customer_id"] or c["crm_customer_id"], assigned_agent_id=c["assigned_agent_id"],
        agent_certified=bool(c["agent_certified"]), amount=float(c["total_overdue"] or 0.0), flags=flags,
        signal=hardship.HardshipSignal(level, bool(c["vulnerable"])), team=c["assigned_team"],
        history=hardship.HardshipHistory(c["plan_end"], bool(c["ever_offered"]), c["last_offer_ts"]),
        options=hardship.options(programs, as_of, ctx, level), **model)


def build(con, with_capacity: bool = True) -> tuple[list[dict], dict]:
    from trustissues.nba.train import CASE_MODEL_NAME, MODEL_NAME

    sources.register(con)
    risk, reasons, reach, snapshot_id = score_cases(con, config.p3_dir() / "snapshots")
    promise_risk = score_open_promises(con, config.p3_dir() / "snapshots")
    cases = load_cases(con, config.AS_OF_DATE)
    flags = rules.load_case_flags(con, [c["case_id"] for c in cases], contact_day=config.AS_OF_DATE)
    cal = actions.load_calibration()
    costs = actions.load_unit_costs(con)
    if config.settings()["p3"].get("call_cost_cad") is not None:
        costs["phone"] = float(config.settings()["p3"]["call_cost_cad"])
    ctx = Context(as_of=config.AS_OF_DATE, costs=costs, effect=cal["per_contact"],
                  base_risk=cal["base_risk"], high_balance=float(config.settings()["p3"]["high_balance_cad"]),
                  model_version=f"{CASE_MODEL_NAME}+{MODEL_NAME}", snapshot_id=snapshot_id)
    programs = hardship.load_programs(con)
    uses = load_uses_12m(con, config.AS_OF_DATE)
    plans = [plan_case(case_input(
        c, flags[c["case_id"]], programs, uses, config.AS_OF_DATE, risk=risk[c["case_id"]],
        reasons=reasons[c["case_id"]], reach=reach[c["case_id"]], promise_risk=promise_risk.get(c["current_ptp_id"]),
    ), ctx) for c in cases]
    same_day = one_contact_per_customer(plans)

    capacity = None
    if with_capacity:
        cap = allocate.load_capacity(con, config.AS_OF_DATE)
        capacity = {"start": {"channel": dict(cap.channel), "agent_calls": sum(cap.agent_calls.values()),
                              "agents_working": len(cap.agent_calls)}}
        requests = [request(p) for p in plans]
        assignments = allocate.allocate(requests, cap)
        # a non-call keeps the routing rules (assigned agent for wait, certified routing for hardship)
        assignments = [a if a.action in ("agent_call", hardship.SUPPORT) else
                       allocate.Assignment(a.action, default_agent(p, a.action), a.no_capacity)
                       for p, a in zip(plans, assignments)]
        # every hardship / vulnerable case without a certified agent yet gets a certified owner
        need = [i for i, (p, a) in enumerate(zip(plans, assignments)) if p.route_to_certified and a.agent_id is None]
        for i, owner in zip(need, allocate.assign_owners([requests[i] for i in need], cap)):
            assignments[i] = allocate.Assignment(assignments[i].action, owner, assignments[i].no_capacity)
        capacity["left"] = {"channel": dict(cap.channel), "agent_calls": sum(cap.agent_calls.values())}
    else:
        assignments = [allocate.Assignment(p.order[0], default_agent(p, p.order[0]), []) for p in plans]
    rows = [render(p, a, ctx) for p, a in zip(plans, assignments)]
    return rows, {"snapshot_id": snapshot_id, "scored_cases": len(risk), "scored_promises": len(promise_risk),
                  "same_day_waits": same_day,
                  "calibration": cal, "capacity": capacity, "unconstrained": [p.order[0] for p in plans]}


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


def write_report(path, rows: list[dict], info: dict, example: dict) -> None:
    import collections

    import duckdb

    from trustissues.nba.evaluate import write_section

    q = duckdb.connect()
    f = f"read_parquet('{path.as_posix()}')"
    by_action = q.execute(f"""
        SELECT action, count(*), avg(risk_score), avg(expected_value), count(*) FILTER (WHERE requires_approval)
        FROM {f} GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    unconstrained = collections.Counter(info["unconstrained"])
    by_reason = q.execute(f"SELECT coalesce(approval_reason, 'none'), count(*) FROM {f} GROUP BY 1 ORDER BY 2 DESC").fetchall()
    rules_hit = q.execute(f"""
        SELECT r, count(DISTINCT case_id) FROM (
            SELECT case_id, unnest(regexp_extract_all(decision_card, '"rule": "([a-z_0-9]+)"', 1)) AS r FROM {f})
        GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    flagged = f"""(SELECT *, json_extract_string(decision_card, '$.why_beat_runner_up') AS why,
                         from_json(json_extract(decision_card, '$.blocked_actions'),
                                   '[{{"action": "VARCHAR", "rule": "VARCHAR"}}]') AS b,
                         json_array_length(json_extract(decision_card, '$.hardship_options')) AS n_options
                  FROM {f} WHERE json_extract_string(decision_card, '$.summary') LIKE '%Hardship or vulnerability:%')"""
    by_situation = q.execute(f"""
        SELECT regexp_replace(split_part(why, ', but ', 1), ' \\(POL-COLL-004 (§[0-9.]+)\\)', ' (\\1)') AS situation,
               count(*) FILTER (WHERE action = 'support_call'), count(*) FILTER (WHERE why LIKE '%, but %'),
               count(*) FILTER (WHERE action = 'wait' AND why NOT LIKE '%, but %'), count(*) FILTER (WHERE n_options > 0)
        FROM {flagged} GROUP BY 1 ORDER BY count(*) DESC""").fetchall()
    support_blocked = q.execute(f"""
        SELECT list_filter(b, x -> x.action = 'support_call')[1].rule, count(*) FROM {flagged}
        WHERE action = 'wait' AND why LIKE '%, but %' GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    n_flagged, n_support, n_owned, n_reminded = q.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE action = 'support_call'), count(agent_id),
               count(*) FILTER (WHERE action NOT IN ('wait', 'support_call')) FROM {flagged}""").fetchone()
    t5 = "\n".join(f"| {s} | {c:,} | {bl:,} | {w:,} | {o:,} |" for s, c, bl, w, o in by_situation)
    t6 = "\n".join(f"| `{r}` | {n:,} |" for r, n in support_blocked)
    cap = info["capacity"]
    t1 = "\n".join(f"| `{a}` | {unconstrained.get(a, 0):,} | {c:,} | {r:.2f} | {v:,.2f} | {ap:,} |"
                   for a, c, r, v, ap in by_action)
    t2 = "\n".join(f"| {r} | {c:,} |" for r, c in by_reason)
    t3 = "\n".join(f"| `{r}` | {c:,} |" for r, c in rules_hit)
    start, left = cap["start"]["channel"], cap["left"]["channel"]
    t4 = "\n".join(f"| {ch} | {start[ch]:,.0f} | {start[ch] - left.get(ch, 0):,.0f} |" for ch in sorted(start))
    cal = info["calibration"]
    write_section("nba_queue", f"""
## Morning NBA queue (`nba/queue.py`)

{len(rows):,} open cases on {config.AS_OF_DATE}, one decision each, in the `gold.nba_decisions` shape
(`warehouse/p3/nba_decisions.parquet`). Every case has a risk from the case model (chance of not being paid up
within 30 days; feature snapshot `{info['snapshot_id']}`); {info['scored_promises']:,} also have an open
promise scored by the promise model (shown on the card).

Value of a contact = {cal['per_contact']:+.2%} cure per contact (the bank's randomised test) x the case's risk /
{cal['base_risk']:.1%} x its reach on the channel x amount overdue - unit cost. The effect is small and its
interval includes zero, so cheap channels the customer responds to win, and a call has to earn its cost.

| Action | Best before capacity | Final | Avg risk | Avg expected value (CAD) | Need approval |
|---|---|---|---|---|---|
{t1}

One contact per person per day: {info.get('same_day_waits', 0):,} cases wait because their person is contacted on
another case today ("Best before capacity" counts after this rule).

Capacity on {config.AS_OF_DATE} (`channel_capacity`; {cap['start']['agents_working']} agents on shift with
{cap['start']['agent_calls']:,} outbound calls between them, {cap['left']['agent_calls']:,} left unused):

| Channel | Capacity | Used |
|---|---|---|
{t4}

| Approval reason | Cases |
|---|---|
{t2}

### Hardship and vulnerable cases (`nba/hardship.py`, POL-COLL-004)

{n_flagged:,} open cases have a hardship or vulnerability flag. None gets a payment reminder or a collections call
({n_reminded:,}); {n_owned:,} have a certified owner; {n_support:,} get a support call today (a certified agent
offering help, hardship programmes first). The rest wait: nothing is due, or a contact rule blocks the call.

| Situation | Support call today | Due, blocked by a rule | Nothing due today | With eligible programmes on the card |
|---|---|---|---|---|
{t5}

A support call was due but a contact rule blocked it:

| Rule | Cases |
|---|---|
{t6}

Cases where a rule (or capacity) removed at least one action:

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
    print(f"wrote {len(rows):,} decisions to {out} (case risk for {info['scored_cases']:,} open cases, "
          f"{info['scored_promises']:,} open promises scored, feature snapshot {info['snapshot_id']})")
    print(duckdb.connect().sql(f"""
        SELECT action, count(*) AS cases, round(avg(risk_score), 3) AS avg_risk,
               round(avg(expected_value), 2) AS avg_value, count(*) FILTER (WHERE requires_approval) AS need_approval
        FROM read_parquet('{out.as_posix()}') GROUP BY 1 ORDER BY 2 DESC"""))
    example = next((r for r in rows if r["action"] == "agent_call"), rows[0])
    print("example decision card:\n" + json.dumps(json.loads(example["decision_card"]), indent=2))
    write_report(out, rows, info, json.loads(example["decision_card"]))
    if args.write_gold:
        n = write_gold(out.as_posix(), config.AS_OF_DATE)
        print(f"gold.nba_decisions now has {n:,} rows for {config.AS_OF_DATE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
