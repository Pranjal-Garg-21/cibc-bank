"""Action catalogue, valuing and choosing an action, and the historical data the values come from.

    python -m trustissues.nba.actions      # writes warehouse/p3/action_values.parquet + report section

Harshness orders actions by collections pressure: hardship may only move a case towards lower
harshness (nba/hardship.py). `support_call` is a certified agent calling to offer help (hardship programmes
first, no payment request before an option is offered, POL-COLL-004 §4.2/§4.5): it is contact, so every contact
rule applies, but it puts no collections pressure on the customer (harshness 0). It is only for hardship and
vulnerable cases and is never valued with the collections effect below.

Each other allowed action is valued as the extra chance of the case curing (paid up and closed) within 30 days
that the contact buys, times the amount overdue, minus its unit cost (`value_actions`).

Where the effect comes from (`calibrate`, saved to action_calibration.json):
- The size of a contact's effect comes from the bank's randomised test: randomly assigned cases in
  no_contact_holdout get fewer treated contacts; the cure difference per extra contact is the effect.
- The historical rates after each treatment (`action_values.parquet`) are NOT used as effects: the bank
  chose who got which treatment, and by cure every treatment looks worse than waiting (the bank contacts
  the cases that are not curing). They are reported, not acted on.
- One experiment cannot separate channels, so every channel gets the same effect, scaled by `reach`: how
  well this customer responds on that channel (answered calls, opened emails, clicked SMS links, opened
  pushes in the last 30 days) relative to the average customer. Cost then decides between channels.
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
    Action("support_call", "phone", 0, None),  # certified agent, hardship / vulnerable cases only
    Action("push_reminder", "push", 1, "push"),
    Action("email_reminder", "email", 1, "email_reminder"),
    Action("sms_reminder", "sms", 2, "payment_link"),
    Action("letter", "mail", 2, "letter"),
    Action("agent_call", "phone", 3, "outbound_call"),
]}


NOT_VALUED = ("wait", "support_call")  # wait is the baseline; a support call is policy, not a collections bet


def harshness(action: str) -> int:
    return CATALOGUE[action].harshness


def choose(allowed: list[str], values: dict[str, float]) -> str:
    """Highest value among allowed actions; ties go to the gentler action. Unvalued actions lose to wait."""
    if "wait" not in allowed:
        allowed = ["wait", *allowed]
    return max(allowed, key=lambda a: (values.get(a, 0.0 if a == "wait" else float("-inf")), -harshness(a)))


ACTION_VALUES_FILE = "action_values.parquet"
CALIBRATION_FILE = "action_calibration.json"
CAPACITY_CHANNEL = {"call": "phone", "letter": "mail"}  # channel_capacity names -> team channel names
HORIZON = 30
# Outcomes are recorded with a lag: only 30-day windows that end by the last recorded cure are used.
CUTOFF = "(SELECT max(cure_date) FROM src_case_history)"


def load_unit_costs(con) -> dict[str, float]:
    """channel -> average unit cost in CAD (channel_capacity.unit_cost_cad), in the team's channel names."""
    from trustissues.features import sources

    rows = sources.channel_capacity(con).aggregate("channel, avg(unit_cost_cad)").fetchall()
    return {CAPACITY_CHANNEL.get(ch, ch): cost for ch, cost in rows}


def value_actions(allowed: list[str], risk: float, amount: float, costs: dict[str, float], effect: float,
                  base_risk: float = 1.0, reach: dict[str, float] | None = None) -> dict[str, float]:
    """Expected net value in CAD of each allowed action, relative to waiting.

    value = effect x (risk / base_risk) x reach[action] x amount - unit cost

    effect: extra chance of curing per contact for the average case (randomised test).
    risk / base_risk: a case that would cure anyway gains little; one at high risk gains more.
    reach: this customer's response on the action's channel relative to the average customer (1 = average).
    """
    reach = reach or {}
    values = {"wait": 0.0}
    for a in allowed:
        if a in NOT_VALUED:
            continue
        gain = effect * (risk / base_risk) * reach.get(a, 1.0)
        values[a] = gain * amount - costs.get(CATALOGUE[a].channel, 0.0)
    return values


# action -> the case feature measuring how this customer responds on that channel (last 30 days)
REACH_FEATURES = {"agent_call": "call_rpc_rate_30d", "email_reminder": "email_open_rate_30d",
                  "sms_reminder": "sms_click_rate_30d", "push_reminder": "push_open_rate_30d"}


def reach(features: dict, averages: dict[str, float]) -> dict[str, float]:
    """Response on each channel relative to the average customer, shrunk halfway to average (1.0).
    No recent contacts on a channel = average. Letters have no response data: average."""
    out = {}
    for a, f in REACH_FEATURES.items():
        v, avg = features.get(f), averages.get(f)
        out[a] = 1.0 if v is None or v != v or not avg else (v + avg) / (2 * avg)
    return out


BUCKETS = """CASE WHEN {d} IS NULL THEN NULL WHEN {d} <= 0 THEN 'current' WHEN {d} <= 30 THEN '1-30'
                  WHEN {d} <= 60 THEN '31-60' WHEN {d} <= 90 THEN '61-90' WHEN {d} <= 120 THEN '91-120'
                  WHEN {d} <= 150 THEN '121-150' WHEN {d} <= 180 THEN '151-180' ELSE '180+' END"""


def action_values_sql() -> str:
    """Per treatment_code x test_cell x dpd bucket: contacts, promise rate, payment and cure within 30 days, cost.

    Within 30 days of a contact, in the same case: paid = a kept / partially kept promise paid, or a payment
    taken on a contact (amount = the larger of the two sums, they can describe the same payment); cured = the
    case was paid up and closed. `wait` = cases in no_contact_holdout, measured from case open. Only contacts
    whose 30-day window is fully observed are counted.
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
    cures AS (SELECT case_id, cure_date FROM src_case_history WHERE cure_date IS NOT NULL),
    treated AS (
        SELECT c.contact_id, c.case_id, c.treatment_code, c.test_cell, c.bucket_at_contact AS dpd_bucket,
               CAST(c.contact_ts AS DATE) AS d0, coalesce(c.ptp_made_flag, false) AS ptp_made,
               coalesce(c.contact_cost_cad, 0) AS cost
        FROM src_contacts c
        WHERE c.treatment_code IS NOT NULL AND c.test_cell IS NOT NULL AND c.case_id IS NOT NULL
          AND CAST(c.contact_ts AS DATE) + {HORIZON} <= {CUTOFF}
    ),
    outcome AS (
        SELECT t.contact_id,
               greatest((SELECT coalesce(sum(p.amount), 0) FROM paid p
                         WHERE p.case_id = t.case_id AND p.paid_date BETWEEN t.d0 AND t.d0 + {HORIZON}),
                        (SELECT coalesce(sum(k.amount), 0) FROM taken k
                         WHERE k.case_id = t.case_id AND k.paid_date BETWEEN t.d0 AND t.d0 + {HORIZON})) AS paid_30d,
               EXISTS (SELECT 1 FROM cures u WHERE u.case_id = t.case_id
                       AND u.cure_date BETWEEN t.d0 AND t.d0 + {HORIZON}) AS cured_30d
        FROM treated t
    ),
    contacts AS (
        SELECT t.treatment_code, t.test_cell, t.dpd_bucket, count(*) AS contacts,
               count(DISTINCT t.case_id) AS cases, avg(CAST(t.ptp_made AS INTEGER)) AS promise_rate,
               avg(CAST(o.paid_30d > 0 AS INTEGER)) AS paid_30d_rate, avg(o.paid_30d) AS avg_paid_30d,
               avg(CAST(o.cured_30d AS INTEGER)) AS cure_30d_rate, avg(t.cost) AS avg_contact_cost_cad
        FROM treated t JOIN outcome o USING (contact_id)
        GROUP BY ALL
    ),
    holdout AS (
        SELECT c.case_id, {BUCKETS.format(d="c.entry_dpd")} AS dpd_bucket, c.case_open_date AS d0
        FROM src_cases c WHERE c.test_cell = 'no_contact_holdout' AND c.case_open_date + {HORIZON} <= {CUTOFF}
    ),
    wait AS (
        SELECT 'wait' AS treatment_code, 'no_contact_holdout' AS test_cell, h.dpd_bucket,
               count(*) AS contacts, count(*) AS cases, NULL::DOUBLE AS promise_rate,
               avg(CAST(o.paid_30d > 0 AS INTEGER)) AS paid_30d_rate, avg(o.paid_30d) AS avg_paid_30d,
               avg(CAST(o.cured_30d AS INTEGER)) AS cure_30d_rate, 0.0 AS avg_contact_cost_cad
        FROM holdout h JOIN (
            SELECT h.case_id,
                   greatest((SELECT coalesce(sum(p.amount), 0) FROM paid p
                             WHERE p.case_id = h.case_id AND p.paid_date BETWEEN h.d0 AND h.d0 + {HORIZON}),
                            (SELECT coalesce(sum(k.amount), 0) FROM taken k
                             WHERE k.case_id = h.case_id AND k.paid_date BETWEEN h.d0 AND h.d0 + {HORIZON})) AS paid_30d,
                   EXISTS (SELECT 1 FROM cures u WHERE u.case_id = h.case_id
                           AND u.cure_date BETWEEN h.d0 AND h.d0 + {HORIZON}) AS cured_30d
            FROM holdout h) o USING (case_id)
        GROUP BY ALL
    )
    SELECT * FROM contacts UNION ALL SELECT * FROM wait
    ORDER BY treatment_code, test_cell, dpd_bucket
    """


def cell_summary_sql() -> str:
    """Case level, randomly assigned cases only, 30 days from case open: contacts, payment and cure by test cell.

    About 60% of champion / challenger cases were assigned by rule, which would confound the comparison."""
    return f"""
    WITH paid AS (
        SELECT case_id, paid_date FROM src_promise_outcomes
        WHERE ptp_status IN ('kept', 'partially_kept') AND paid_date IS NOT NULL
    ),
    c AS (
        SELECT c.case_id, c.test_cell, c.case_open_date AS d0, h.cure_date
        FROM src_cases c JOIN src_case_history h USING (case_id)
        WHERE c.assignment_method = 'random' AND c.case_open_date + {HORIZON} <= {CUTOFF}
    ),
    n AS (
        SELECT c.case_id, count(x.contact_id) AS treated
        FROM c LEFT JOIN src_contacts x ON x.case_id = c.case_id AND x.treatment_code IS NOT NULL
         AND CAST(x.contact_ts AS DATE) BETWEEN c.d0 AND c.d0 + {HORIZON}
        GROUP BY 1
    )
    SELECT c.test_cell, count(*) AS cases, avg(n.treated) AS treated_contacts,
           avg(CAST(EXISTS (SELECT 1 FROM paid p WHERE p.case_id = c.case_id
                            AND p.paid_date BETWEEN c.d0 AND c.d0 + {HORIZON}) AS INTEGER)) AS paid_30d_rate,
           avg(CAST(coalesce(c.cure_date BETWEEN c.d0 AND c.d0 + {HORIZON}, false) AS INTEGER)) AS cure_30d_rate
    FROM c JOIN n USING (case_id)
    GROUP BY 1 ORDER BY 1
    """


def calibrate(con) -> dict:
    """Effect of one extra treated contact on curing within 30 days, from the randomised test.

    Randomly assigned cases, first 30 days from open: contacted cells vs no_contact_holdout. The holdout gets
    fewer treated contacts (`contrast`, per treatment); the cure difference divided by the total extra
    contacts is the per-contact effect, with its 95% interval.
    """
    import math

    rows = con.execute(f"""
        WITH c AS (
            SELECT c.case_id, c.test_cell = 'no_contact_holdout' AS held, c.case_open_date AS d0, h.cure_date
            FROM src_cases c JOIN src_case_history h USING (case_id)
            WHERE c.assignment_method = 'random' AND c.case_open_date + {HORIZON} <= {CUTOFF}
        )
        SELECT held, count(*), avg(CAST(coalesce(cure_date BETWEEN d0 AND d0 + {HORIZON}, false) AS INTEGER))
        FROM c GROUP BY 1""").fetchall()
    stats = {held: (n, rate) for held, n, rate in rows}
    (n1, p1), (n0, p0) = stats[False], stats[True]
    effect = p1 - p0
    se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    per_case = con.execute(f"""
        WITH c AS (
            SELECT case_id, test_cell = 'no_contact_holdout' AS held, case_open_date AS d0 FROM src_cases
            WHERE assignment_method = 'random' AND case_open_date + {HORIZON} <= {CUTOFF}
        ),
        k AS (SELECT held, count(*) AS cases FROM c GROUP BY 1)
        SELECT x.treatment_code, c.held, count(*) / any_value(k.cases)
        FROM c JOIN k USING (held)
        JOIN src_contacts x ON x.case_id = c.case_id AND x.treatment_code IS NOT NULL
         AND CAST(x.contact_ts AS DATE) BETWEEN c.d0 AND c.d0 + {HORIZON}
        GROUP BY 1, 2""").fetchall()
    by_code = {a.treatment_code: name for name, a in CATALOGUE.items() if a.treatment_code}
    contrast: dict[str, float] = {}
    for code, held, per in per_case:
        a = by_code.get(code)
        if a:
            contrast[a] = contrast.get(a, 0.0) + (-per if held else per)
    extra = sum(contrast.values())
    return {"cases_contacted": n1, "cases_holdout": n0, "cure_contacted": p1, "cure_holdout": p0,
            "effect": effect, "effect_se": se, "contrast": contrast, "extra_contacts": extra,
            "per_contact": effect / extra, "per_contact_lo": (effect - 1.96 * se) / extra,
            "per_contact_hi": (effect + 1.96 * se) / extra, "base_risk": 1 - p1}


def load_calibration(path=None) -> dict:
    import json

    path = path or config.p3_dir() / CALIBRATION_FILE
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    import json

    from trustissues.features import sources
    from trustissues.nba.evaluate import write_section

    con = connect(read_only=True)
    sources.register(con)
    out = config.p3_dir() / ACTION_VALUES_FILE
    con.execute(f"COPY ({action_values_sql()}) TO '{out.as_posix()}' (FORMAT PARQUET)")
    print(f"wrote {out}")
    cal = calibrate(con)
    (config.p3_dir() / CALIBRATION_FILE).write_text(json.dumps(cal, indent=2), encoding="utf-8")
    print("calibration:", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in cal.items()})
    by_treatment = con.execute(f"""
        SELECT treatment_code, sum(contacts), sum(contacts * promise_rate) / sum(contacts),
               sum(contacts * paid_30d_rate) / sum(contacts), sum(contacts * cure_30d_rate) / sum(contacts),
               sum(contacts * avg_contact_cost_cad) / sum(contacts)
        FROM read_parquet('{out.as_posix()}') GROUP BY 1 ORDER BY 5 DESC""").fetchall()
    cells = con.execute(cell_summary_sql()).fetchall()
    for row in by_treatment + cells:
        print(row)

    def pct(v):
        return "" if v is None else f"{v:.1%}"

    t1 = "\n".join(f"| `{t}` | {n:,} | {pct(pr)} | {pct(pd)} | {pct(cu)} | {cost:.3f} |"
                   for t, n, pr, pd, cu, cost in by_treatment)
    t2 = "\n".join(f"| `{c}` | {n:,} | {tc:.2f} | {pct(pd)} | {pct(cu)} |" for c, n, tc, pd, cu in cells)
    t3 = "\n".join(f"| `{a}` | {d:+.2f} |" for a, d in sorted(cal["contrast"].items(), key=lambda t: -t[1]))
    write_section("action_values", f"""
## Action values: what a contact is worth (`nba/actions.py`)

**Historical rates by treatment** (`contact_history`; `wait` = the no-contact holdout cases from case open).
Within 30 days of the contact, same case; only fully observed windows. Full table by treatment x test cell x
DPD bucket: `warehouse/p3/action_values.parquet`.

| Treatment | Contacts / cases | Promise rate | Paid within 30 days | Cured within 30 days | Avg contact cost (CAD) |
|---|---|---|---|---|---|
{t1}

These are **observational** and are **not used to value actions**: the bank chose who got which treatment.
By cure, every treatment looks worse than waiting, because the bank contacts the cases that are not curing.

**The randomised test** (randomly assigned cases only, 30 days from case open):

| Test cell | Cases | Treated contacts per case | Paid within 30 days | Cured within 30 days |
|---|---|---|---|---|
{t2}

Contacted cells vs `no_contact_holdout`: cure {cal['cure_contacted']:.1%} vs {cal['cure_holdout']:.1%}, a
difference of {cal['effect']:+.2%} (95% interval {cal['effect'] - 1.96 * cal['effect_se']:+.2%} to
{cal['effect'] + 1.96 * cal['effect_se']:+.2%}). The holdout is not contact-free; it gets
{cal['extra_contacts']:.2f} fewer treated contacts per case:

| Treatment | Extra contacts per case in the contacted cells |
|---|---|
{t3}

**Effect used to value actions: {cal['per_contact']:+.2%} cure per extra contact** (95% interval
{cal['per_contact_lo']:+.2%} to {cal['per_contact_hi']:+.2%}), for a case at the average not-cured rate of
{cal['base_risk']:.1%}, scaled by the case's own risk and by its reach on each channel. The interval includes
zero: the data cannot show that contacting helps at all, only that any effect is small. One experiment cannot
separate channels, so channels differ only by reach and cost. Caveats: the test covers cases entering at
1-30 DPD and the bank's contact mix; the same effect is assumed for later stages.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
