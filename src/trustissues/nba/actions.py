"""Action catalogue, valuing and choosing an action, and the historical data the values come from.

    python -m trustissues.nba.actions      # writes warehouse/p3/action_values.parquet + report section

Harshness orders actions by collections pressure: hardship may only move a case towards lower
harshness (nba/hardship.py). Each allowed action is valued as expected extra payment minus its unit
cost (`value_actions`), from historical payment rates by treatment and DPD bucket (`action_values.parquet`,
built from contact_history and the test cells) and channel_capacity.unit_cost_cad.
"""
from __future__ import annotations

from dataclasses import dataclass

from trustissues.common import config
from trustissues.common.db import connect


# Names follow the team's decision-card scheme (common/fixtures.py, contracts/interfaces/stubs.sql):
# actions agent_call / sms_reminder / ... / wait; channels phone / sms / email / mail (+ push).
@dataclass(frozen=True)
class Action:
    name: str
    channel: str | None  # consent channel; None = no contact
    harshness: int  # 0 = no contact ... 3 = outbound collections call
    treatment_code: str | None  # contact_history.treatment_code for the same treatment

    @property
    def contact(self) -> bool:
        return self.channel is not None


CATALOGUE: dict[str, Action] = {a.name: a for a in [
    Action("wait", None, 0, None),
    Action("push_reminder", "push", 1, "push"),
    Action("email_reminder", "email", 1, "email_reminder"),
    Action("sms_reminder", "sms", 2, "payment_link"),
    Action("letter", "mail", 2, "letter"),
    Action("agent_call", "phone", 3, "outbound_call"),
]}


def harshness(action: str) -> int:
    return CATALOGUE[action].harshness


def choose(allowed: list[str], values: dict[str, float]) -> str:
    """Highest value among allowed actions; ties go to the gentler action. Unvalued actions lose to wait."""
    if "wait" not in allowed:
        allowed = ["wait", *allowed]
    return max(allowed, key=lambda a: (values.get(a, 0.0 if a == "wait" else float("-inf")), -harshness(a)))


ACTION_VALUES_FILE = "action_values.parquet"
CAPACITY_CHANNEL = {"call": "phone", "letter": "mail"}  # channel_capacity names -> team channel names
MIN_CONTACTS = 500  # below this, a treatment x bucket payment rate is too noisy to act on


def load_payment_rates(path=None) -> dict[str, dict[str, float]]:
    """dpd bucket -> action -> share of contacts followed by a payment within 30 days (all test cells pooled).

    `wait` is measured only where the no-contact holdout exists (bucket 1-30, where it pays at the rate of
    the least effective treatment: 17.6% vs letter 17.5%). Elsewhere `wait` is estimated as the least
    effective treatment with enough contacts in that bucket.
    """
    import duckdb

    path = path or config.p3_dir() / ACTION_VALUES_FILE
    by_code = {a.treatment_code: name for name, a in CATALOGUE.items() if a.treatment_code}
    by_code["wait"] = "wait"
    rows = duckdb.connect().execute(f"""
        SELECT dpd_bucket, treatment_code, sum(contacts), sum(contacts * paid_30d_rate) / sum(contacts)
        FROM read_parquet('{path.as_posix()}') WHERE dpd_bucket IS NOT NULL GROUP BY ALL""").fetchall()
    rates: dict[str, dict[str, float]] = {}
    for bucket, code, n, rate in rows:
        if code in by_code and n >= MIN_CONTACTS:
            rates.setdefault(bucket, {})[by_code[code]] = rate
    for r in rates.values():
        if "wait" not in r and r:
            r["wait"] = min(r.values())
    return rates


def load_unit_costs(con) -> dict[str, float]:
    """channel -> average unit cost in CAD (channel_capacity.unit_cost_cad), in the team's channel names."""
    from trustissues.features import sources

    rows = sources.channel_capacity(con).aggregate("channel, avg(unit_cost_cad)").fetchall()
    return {CAPACITY_CHANNEL.get(ch, ch): cost for ch, cost in rows}


def value_actions(allowed: list[str], bucket: str | None, risk: float, amount: float,
                  rates: dict[str, dict[str, float]], costs: dict[str, float]) -> dict[str, float]:
    """Expected net value in CAD of each allowed action, relative to waiting.

    value = risk x (payment rate after the action - payment rate when waiting) x amount at stake - unit cost

    risk scales the benefit: contacting someone who would pay anyway adds little. Payment rates are
    historical and observational (treatments were targeted), so this ranks actions; it does not prove
    uplift. Actions with no reliable rate for the bucket get no value and are never chosen over wait.
    """
    r = rates.get(bucket or "", {})
    base = r.get("wait")
    values = {"wait": 0.0}
    if base is None:
        return values
    for a in allowed:
        spec = CATALOGUE[a]
        if a == "wait" or a not in r:
            continue
        values[a] = risk * (r[a] - base) * amount - costs.get(spec.channel, 0.0)
    return values


BUCKETS = """CASE WHEN {d} IS NULL THEN NULL WHEN {d} <= 0 THEN 'current' WHEN {d} <= 30 THEN '1-30'
                  WHEN {d} <= 60 THEN '31-60' WHEN {d} <= 90 THEN '61-90' WHEN {d} <= 120 THEN '91-120'
                  WHEN {d} <= 150 THEN '121-150' WHEN {d} <= 180 THEN '151-180' ELSE '180+' END"""


def action_values_sql() -> str:
    """Per treatment_code x test_cell x dpd bucket: contacts, promise rate, payment within 30 days, cost.

    Payment within 30 days of a contact, in the same case: a kept / partially kept promise paid in that
    window, or a contact in that window where a payment was taken. Amount = the larger of the two sums
    (they can describe the same payment). `wait` = cases in the no_contact_holdout cell, measured from
    case open over the same 30 days.
    """
    return f"""
    WITH paid AS (
        SELECT case_id, paid_date, amount_paid_against AS amount FROM src_promise_outcomes
        WHERE ptp_status IN ('kept', 'partially_kept') AND paid_date IS NOT NULL
    ),
    taken AS (
        SELECT case_id, CAST(contact_ts AS DATE) AS paid_date, payment_amount AS amount FROM src_contacts
        WHERE payment_taken_flag
    ),
    treated AS (
        SELECT c.contact_id, c.case_id, c.treatment_code, c.test_cell, c.bucket_at_contact AS dpd_bucket,
               CAST(c.contact_ts AS DATE) AS d0, coalesce(c.ptp_made_flag, false) AS ptp_made,
               coalesce(c.contact_cost_cad, 0) AS cost
        FROM src_contacts c
        WHERE c.treatment_code IS NOT NULL AND c.test_cell IS NOT NULL AND c.case_id IS NOT NULL
    ),
    outcome AS (
        SELECT t.contact_id,
               greatest((SELECT coalesce(sum(p.amount), 0) FROM paid p
                         WHERE p.case_id = t.case_id AND p.paid_date BETWEEN t.d0 AND t.d0 + 30),
                        (SELECT coalesce(sum(k.amount), 0) FROM taken k
                         WHERE k.case_id = t.case_id AND k.paid_date BETWEEN t.d0 AND t.d0 + 30)) AS paid_30d
        FROM treated t
    ),
    contacts AS (
        SELECT t.treatment_code, t.test_cell, t.dpd_bucket, count(*) AS contacts,
               count(DISTINCT t.case_id) AS cases, avg(CAST(t.ptp_made AS INTEGER)) AS promise_rate,
               avg(CAST(o.paid_30d > 0 AS INTEGER)) AS paid_30d_rate, avg(o.paid_30d) AS avg_paid_30d,
               avg(t.cost) AS avg_contact_cost_cad
        FROM treated t JOIN outcome o USING (contact_id)
        GROUP BY ALL
    ),
    holdout AS (
        SELECT c.case_id, {BUCKETS.format(d="c.entry_dpd")} AS dpd_bucket, c.case_open_date AS d0
        FROM src_cases c WHERE c.test_cell = 'no_contact_holdout'
    ),
    wait AS (
        SELECT 'wait' AS treatment_code, 'no_contact_holdout' AS test_cell, h.dpd_bucket,
               count(*) AS contacts, count(*) AS cases, NULL::DOUBLE AS promise_rate,
               avg(CAST(o.paid_30d > 0 AS INTEGER)) AS paid_30d_rate, avg(o.paid_30d) AS avg_paid_30d,
               0.0 AS avg_contact_cost_cad
        FROM holdout h JOIN (
            SELECT h.case_id,
                   greatest((SELECT coalesce(sum(p.amount), 0) FROM paid p
                             WHERE p.case_id = h.case_id AND p.paid_date BETWEEN h.d0 AND h.d0 + 30),
                            (SELECT coalesce(sum(k.amount), 0) FROM taken k
                             WHERE k.case_id = h.case_id AND k.paid_date BETWEEN h.d0 AND h.d0 + 30)) AS paid_30d
            FROM holdout h) o USING (case_id)
        GROUP BY ALL
    )
    SELECT * FROM contacts UNION ALL SELECT * FROM wait
    ORDER BY treatment_code, test_cell, dpd_bucket
    """


def cell_summary_sql() -> str:
    """Case-level 30-day payment rate by test cell (the plan's §11 question: do the challengers beat champion?).

    Randomly assigned cases only (assignment_method = 'random'): about 60% of champion / challenger cases
    were assigned by rule, which would confound the comparison."""
    return """
    WITH paid AS (
        SELECT case_id, paid_date, amount_paid_against AS amount FROM src_promise_outcomes
        WHERE ptp_status IN ('kept', 'partially_kept') AND paid_date IS NOT NULL
    )
    SELECT c.test_cell, count(*) AS cases,
           avg(CAST(EXISTS (SELECT 1 FROM paid p WHERE p.case_id = c.case_id
                            AND p.paid_date BETWEEN c.case_open_date AND c.case_open_date + 30) AS INTEGER)) AS paid_30d_rate,
           avg(CAST(c.cure_flag AS INTEGER)) AS cure_rate
    FROM src_cases c WHERE c.test_cell IS NOT NULL AND c.assignment_method = 'random'
    GROUP BY 1 ORDER BY 1
    """


def main() -> int:
    from trustissues.features import sources
    from trustissues.nba.evaluate import write_section

    con = connect(read_only=True)
    sources.register(con)
    out = config.p3_dir() / ACTION_VALUES_FILE
    con.execute(f"COPY ({action_values_sql()}) TO '{out.as_posix()}' (FORMAT PARQUET)")
    print(f"wrote {out}")
    by_treatment = con.execute(f"""
        SELECT treatment_code, sum(contacts), sum(contacts * promise_rate) / sum(contacts),
               sum(contacts * paid_30d_rate) / sum(contacts), sum(contacts * avg_paid_30d) / sum(contacts),
               sum(contacts * avg_contact_cost_cad) / sum(contacts)
        FROM read_parquet('{out.as_posix()}') GROUP BY 1 ORDER BY 4 DESC""").fetchall()
    cells = con.execute(cell_summary_sql()).fetchall()
    for r in by_treatment:
        print(r)
    for r in cells:
        print(r)

    def pct(v):
        return "" if v is None else f"{v:.1%}"

    t1 = "\n".join(f"| `{t}` | {n:,} | {pct(pr)} | {pct(pd)} | {amt:,.2f} | {cost:.3f} |"
                   for t, n, pr, pd, amt, cost in by_treatment)
    t2 = "\n".join(f"| `{c}` | {n:,} | {pct(pd)} | {pct(cr)} |" for c, n, pd, cr in cells)
    write_section("action_values", f"""
## Action valuation data (first half of `nba/actions.py`)

Historical results by treatment, from `contact_history` (and `no_contact_holdout` cases for `wait`).
Payment within 30 days = a kept / partially kept promise paid in the window, or a payment taken on a
contact in the window, in the same case. Full table by treatment x test cell x DPD bucket:
`warehouse/p3/action_values.parquet`. These rates are **observational within a cell**: treatments were
targeted by the strategy, so a higher rate is not proof that the treatment caused it. Only the
cell-level comparison below is randomised: it keeps the cases with `assignment_method = random`
(all of `no_contact_holdout`, about 40% of the other cells).

| Treatment | Contacts / cases | Promise rate | Paid within 30 days | Avg paid in 30 days (CAD) | Avg contact cost (CAD) |
|---|---|---|---|---|---|
{t1}

By test cell (randomly assigned cases only, case level, from case open):

| Test cell | Cases | Paid within 30 days | Cured |
|---|---|---|---|
{t2}
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
