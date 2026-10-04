"""Where each of today's recommendations stands, read straight from gold.nba_decisions and gold.nba_overrides.

Read-only and independent of P3's modules, so the overview and oversight screens work on any warehouse that has the
two tables. Decisions are never written here (writes go through nba.review). Same rule as nba/review.py: of each kind
of decision the latest row wins, so a changed mind counts once.
"""
from __future__ import annotations

from .rbac import CALL_ACTIONS

# Fallback wording when nba.review is not importable (it is the owner of the list).
REASON_LABELS = {
    "customer_already_paid": "Payment or arrangement already in place",
    "customer_contacted_today": "Customer already reached today",
    "hardship_disclosed": "Customer disclosed hardship that is not on the card",
    "vulnerability_concern": "Signs of vulnerability that are not on the card",
    "dispute_raised": "Customer is disputing the debt",
    "customer_requested_channel": "Customer asked for another channel or time",
    "contact_details_wrong": "Contact details known to be wrong",
    "card_facts_wrong": "A fact on the decision card is wrong",
    "too_much_contact": "Recent contact makes another one inappropriate",
    "other": "None of the above",
}
SAFEGUARD_REASONS = ("hardship_disclosed", "vulnerability_concern", "dispute_raised")


def reason_labels() -> tuple[dict[str, str], tuple[str, ...]]:
    try:
        from trustissues.nba import review
        return dict(review.REASON_CODES), tuple(review.SAFEGUARD_REASONS)
    except ImportError:
        return REASON_LABELS, SAFEGUARD_REASONS


def _has(con, schema: str, table: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                            [schema, table]).fetchone()[0])


def have_decisions(con) -> bool:
    return _has(con, "gold", "nba_decisions")


def state_rows(con, as_of, extra_where: str = "", params: list | None = None) -> list[dict]:
    """Today's recommendations (optionally narrowed by an AND-clause on gold.nba_decisions columns) with `state`:
    ready | waiting_agent | waiting_approval | blocked | overridden. [] if the tables are missing."""
    if not have_decisions(con):
        return []
    overrides = _has(con, "gold", "nba_overrides")
    agent = ("SELECT recommendation_id, arg_max(decision, ts) AS agent_decision FROM gold.nba_overrides "
             "WHERE decision IN ('accepted', 'overridden') GROUP BY 1") if overrides else \
        "SELECT NULL::VARCHAR AS recommendation_id, NULL::VARCHAR AS agent_decision WHERE false"
    approval = ("SELECT recommendation_id, arg_max(decision, ts) AS approval_decision FROM gold.nba_overrides "
                "WHERE decision IN ('approved', 'rejected') GROUP BY 1") if overrides else \
        "SELECT NULL::VARCHAR AS recommendation_id, NULL::VARCHAR AS approval_decision WHERE false"
    calls = ", ".join(f"'{a}'" for a in CALL_ACTIONS)
    cur = con.execute(f"""
        WITH d AS (SELECT * EXCLUDE (decision_card), json_extract_string(decision_card, '$.summary') AS summary,
                          json_extract_string(decision_card, '$.support_reason') AS support_reason
                   FROM gold.nba_decisions WHERE as_of_date = ?{extra_where}),
             ag AS ({agent}), ap AS ({approval})
        SELECT d.*, ag.agent_decision, ap.approval_decision,
               (d.requires_approval AND ap.approval_decision IS NULL AND coalesce(ag.agent_decision, '') <> 'overridden')
                   AS approval_pending,
               CASE WHEN ag.agent_decision = 'overridden' THEN 'overridden'
                    WHEN ap.approval_decision = 'rejected' THEN 'blocked'
                    WHEN d.action IN ({calls}) AND ag.agent_decision IS NULL THEN 'waiting_agent'
                    WHEN d.requires_approval AND ap.approval_decision IS NULL THEN 'waiting_approval'
                    ELSE 'ready' END AS state
        FROM d LEFT JOIN ag USING (recommendation_id) LEFT JOIN ap USING (recommendation_id)""",
                      [as_of, *(params or [])])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = r.get(key)
        k = "none" if k is None else str(k)
        out[k] = out.get(k, 0) + 1
    return out


def decision_rows(con, since, until, agent_ids: list[str] | None = None, kinds: tuple[str, ...] = ("accepted", "overridden")):
    """Rows of gold.nba_overrides decided between two dates (inclusive): agent_id, decision, reason_code, day.
    agent_ids narrows to those deciders (None = everyone). [] if the table is missing."""
    if not _has(con, "gold", "nba_overrides"):
        return []
    marks = ", ".join("?" for _ in kinds)
    where, params = f"decision IN ({marks}) AND CAST(ts AS DATE) BETWEEN ? AND ?", [*kinds, since, until]
    if agent_ids is not None:
        if not agent_ids:
            return []
        where += f" AND agent_id IN ({', '.join('?' for _ in agent_ids)})"
        params += agent_ids
    cur = con.execute(f"SELECT agent_id, decision, reason_code, CAST(ts AS DATE) AS day FROM gold.nba_overrides "
                      f"WHERE {where}", params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def team_agent_ids(con, lead_id: str) -> list[str]:
    """The active agents who report to this team lead (the same rule as the Agent accounts screen)."""
    return [r[0] for r in con.execute("SELECT agent_id FROM curated.agents WHERE team_lead_id = ? AND status = 'active' "
                                      "ORDER BY agent_id", [lead_id]).fetchall()]


def staff_names(con, ids) -> dict[str, str]:
    ids = list(ids)
    if not ids:
        return {}
    return dict(con.execute(f"SELECT agent_id, agent_display_name FROM curated.agents WHERE agent_id IN "
                            f"({', '.join('?' for _ in ids)})", ids).fetchall())
