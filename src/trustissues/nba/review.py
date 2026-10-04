"""Human review of the morning NBA queue: agent accept / override, and approvals (I7, gold.nba_overrides).

    python -m trustissues.nba.review status  [--date D]                  # where each recommendation stands
    python -m trustissues.nba.review approve-batch --lead TL [--date D] [--reject REC=reason ...]
    python -m trustissues.nba.review summary [--from D] [--to D]         # the weekly override review

Every decision is a row in gold.nba_overrides {recommendation_id, agent_id, decision, reason_code, ts}, append
only: the latest row of each kind wins and the history is kept. Two kinds:
- agent: `accepted` or `overridden`, by the agent the recommendation is assigned to. Required for a call
  (`agent_call`, or a certified agent's `support_call`); optional otherwise (an agent may override a `wait`). An override needs a reason code.
- approval: `approved` or `rejected`, by a team lead (hardship and vulnerability: also a hardship specialist).
  Required when the recommendation says so (vulnerability, hardship, high balance, the daily digital batch).
  A rejection needs a reason code. Once the agent has overridden a recommendation it is no longer approved or
  rejected (nothing is left to sign off; it leaves the approval lists). Digital batch recommendations have no
  agent: the team lead's approval is the only sign-off, done for the whole batch at once (`approve_batch`), minus
  any cases pulled out of it.

A recommendation is carried out only when it is `ready`: approved (or not needing approval) and, for a call,
accepted by its agent. Writing needs the warehouse write lock: run it from the app or as a stage, never
alongside the Layer 1 pipeline. No AI anywhere in this module.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime

from trustissues.common import config

# One reason list for agent overrides and approval rejections (interfaces.md open question 5, owned by P3).
REASON_CODES = {
    "customer_already_paid": "Payment or arrangement already in place",
    "customer_contacted_today": "Customer already reached today, inbound or on another channel",
    "hardship_disclosed": "Customer disclosed hardship that is not on the card",
    "vulnerability_concern": "Signs of vulnerability that are not on the card",
    "dispute_raised": "Customer is disputing the debt",
    "customer_requested_channel": "Customer asked to be contacted another way or at another time",
    "contact_details_wrong": "Phone number, email or address known to be wrong",
    "card_facts_wrong": "A fact on the decision card is wrong",
    "too_much_contact": "Recent contact makes another one inappropriate",
    "other": "None of the above (counted in the weekly review)",
}
# Reasons that mean the case should be handled more gently than the card assumed: shown first in the review.
SAFEGUARD_REASONS = ("hardship_disclosed", "vulnerability_concern", "dispute_raised")
AGENT_DECISIONS = ("accepted", "overridden")
APPROVAL_DECISIONS = ("approved", "rejected")
# Who may approve each kind of mandatory approval (agents.role).
APPROVERS = {
    "vulnerability": ("team_lead", "hardship_specialist"),
    "hardship": ("team_lead", "hardship_specialist"),
    "high_balance": ("team_lead",),
    "digital_batch": ("team_lead",),
}
NEEDS_AGENT = ("agent_call", "support_call")  # every call is accepted by its agent


class ReviewError(ValueError):
    """A decision that is not allowed. The message says why."""


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    agent_id: str | None
    action: str
    requires_approval: bool
    approval_reason: str | None


def _recommendation(con, recommendation_id: str) -> Recommendation:
    row = con.execute("""
        SELECT recommendation_id, agent_id, action, coalesce(requires_approval, false), approval_reason
        FROM gold.nba_decisions WHERE recommendation_id = ?""", [recommendation_id]).fetchone()
    if row is None:
        raise ReviewError(f"no recommendation {recommendation_id} in gold.nba_decisions")
    return Recommendation(*row)


def _role(con, user_id: str) -> str | None:
    from trustissues.features import sources

    row = con.execute(f"SELECT role FROM ({sources.sql('agents')}) WHERE agent_id = ? AND status = 'active'",
                      [user_id]).fetchone()
    return row[0] if row else None


def check(rec: Recommendation, user_id: str, role: str | None, decision: str, reason_code: str | None) -> None:
    """Raise ReviewError unless this user may record this decision on this recommendation."""
    if decision not in AGENT_DECISIONS + APPROVAL_DECISIONS:
        raise ReviewError(f"unknown decision {decision!r}: use one of {AGENT_DECISIONS + APPROVAL_DECISIONS}")
    if reason_code is not None and reason_code not in REASON_CODES:
        raise ReviewError(f"unknown reason code {reason_code!r}: use one of {sorted(REASON_CODES)}")
    if role is None:
        raise ReviewError(f"{user_id} is not an active member of staff")
    if decision in ("overridden", "rejected") and reason_code is None:
        raise ReviewError(f"{decision} needs a reason code")
    if decision in ("accepted", "approved") and reason_code is not None:
        raise ReviewError(f"{decision} takes no reason code")
    if decision in AGENT_DECISIONS:
        if rec.agent_id is None:
            raise ReviewError(f"{rec.recommendation_id} has no assigned agent: it is decided by approval")
        if user_id != rec.agent_id:
            raise ReviewError(f"{rec.recommendation_id} is assigned to {rec.agent_id}, not {user_id}")
    else:
        if not rec.requires_approval:
            raise ReviewError(f"{rec.recommendation_id} does not need approval")
        allowed = APPROVERS.get(rec.approval_reason, ("team_lead",))
        if role not in allowed:
            raise ReviewError(f"{rec.approval_reason} approval needs a {' or '.join(allowed)}, not a {role}")


def _agent_decision(con, recommendation_id: str) -> str | None:
    """The latest agent decision (accepted | overridden) on a recommendation, or None."""
    row = con.execute("""SELECT decision FROM gold.nba_overrides WHERE recommendation_id = ?
                         AND decision IN ('accepted', 'overridden') ORDER BY ts DESC LIMIT 1""",
                      [recommendation_id]).fetchone()
    return row[0] if row else None


def record(con, recommendation_id: str, user_id: str, decision: str, reason_code: str | None = None,
           ts: datetime | None = None) -> dict:
    """Validate one decision and append it to gold.nba_overrides. Returns the row written."""
    rec = _recommendation(con, recommendation_id)
    check(rec, user_id, _role(con, user_id), decision, reason_code)
    if decision in APPROVAL_DECISIONS and _agent_decision(con, recommendation_id) == "overridden":
        # the agent has stopped the action: an approval would change nothing, a rejection would count it twice
        raise ReviewError(f"{recommendation_id} was overridden by its agent: there is nothing to approve or reject")
    row = {"recommendation_id": recommendation_id, "agent_id": user_id, "decision": decision,
           "reason_code": reason_code, "ts": ts or datetime.now()}
    con.execute("INSERT INTO gold.nba_overrides VALUES (?, ?, ?, ?, ?)", list(row.values()))
    return row


def approve_batch(con, lead_id: str, as_of: date, rejected: dict[str, str] | None = None,
                  ts: datetime | None = None) -> dict[str, int]:
    """A team lead signs off the day's digital batch: every digital_batch recommendation of that date without an
    approval decision yet is approved, except those in `rejected` (recommendation_id -> reason code)."""
    rejected = rejected or {}
    role = _role(con, lead_id)
    if role not in APPROVERS["digital_batch"]:
        raise ReviewError(f"the digital batch is approved by a team lead, not {role or 'an inactive user'}")
    ts = ts or datetime.now()
    for rec_id, reason in rejected.items():
        rec = _recommendation(con, rec_id)
        if rec.approval_reason != "digital_batch":
            raise ReviewError(f"{rec_id} is not in the digital batch")
        check(rec, lead_id, role, "rejected", reason)
    con.execute("CREATE OR REPLACE TEMP TABLE _batch_rejected (recommendation_id VARCHAR, reason_code VARCHAR)")
    if rejected:
        con.executemany("INSERT INTO _batch_rejected VALUES (?, ?)", list(rejected.items()))
    open_batch = """
        SELECT d.recommendation_id FROM gold.nba_decisions d
        WHERE d.as_of_date = ? AND d.approval_reason = 'digital_batch'
          AND d.recommendation_id NOT IN (SELECT recommendation_id FROM gold.nba_overrides
                                          WHERE decision IN ('approved', 'rejected'))"""
    con.execute("BEGIN")
    con.execute(f"""
        INSERT INTO gold.nba_overrides
        SELECT b.recommendation_id, ?, CASE WHEN r.recommendation_id IS NULL THEN 'approved' ELSE 'rejected' END,
               r.reason_code, ?
        FROM ({open_batch}) b LEFT JOIN _batch_rejected r USING (recommendation_id)""", [lead_id, ts, as_of])
    con.execute("COMMIT")
    counts = dict(con.execute("""
        SELECT decision, count(*) FROM gold.nba_overrides WHERE agent_id = ? AND ts = ? GROUP BY 1""",
                              [lead_id, ts]).fetchall())
    return {"approved": counts.get("approved", 0), "rejected": counts.get("rejected", 0)}


STATUS_SQL = f"""
    WITH latest AS (
        SELECT recommendation_id, decision, reason_code, agent_id AS decided_by,
               CASE WHEN decision IN ('approved', 'rejected') THEN 'approval' ELSE 'agent' END AS kind
        FROM gold.nba_overrides
        QUALIFY row_number() OVER (PARTITION BY recommendation_id, kind ORDER BY ts DESC) = 1
    )
    SELECT d.recommendation_id, d.case_id, d.agent_id, d.action, d.approval_reason,
           CASE WHEN NOT coalesce(d.requires_approval, false) THEN 'not_needed'
                ELSE coalesce(ap.decision, 'pending') END AS approval,
           coalesce(ag.decision, CASE WHEN d.action IN ({", ".join(f"'{a}'" for a in NEEDS_AGENT)})
                                      AND d.agent_id IS NOT NULL THEN 'pending' ELSE 'not_needed' END) AS agent,
           coalesce(ag.reason_code, ap.reason_code) AS reason_code
    FROM gold.nba_decisions d
    LEFT JOIN latest ap ON ap.recommendation_id = d.recommendation_id AND ap.kind = 'approval'
    LEFT JOIN latest ag ON ag.recommendation_id = d.recommendation_id AND ag.kind = 'agent'
    WHERE d.as_of_date = ?
"""


def status(con, as_of: date):
    """One row per recommendation of that date: approval and agent state, and `state`:
    blocked (approval rejected) | overridden | waiting_approval | waiting_agent | ready."""
    return con.sql(f"""
        SELECT *, CASE WHEN approval = 'rejected' THEN 'blocked'
                       WHEN agent = 'overridden' THEN 'overridden'
                       WHEN approval = 'pending' THEN 'waiting_approval'
                       WHEN agent = 'pending' THEN 'waiting_agent'
                       ELSE 'ready' END AS state
        FROM ({STATUS_SQL})""", params=[as_of])


def summary(con, start: date, end: date) -> dict:
    """The weekly override review (and monitoring.py's override rate), for recommendations dated start..end."""
    base = f"""
        SELECT s.*, d.as_of_date FROM ({STATUS_SQL.replace("WHERE d.as_of_date = ?",
                                                          "WHERE d.as_of_date BETWEEN ? AND ?")}) s
        JOIN gold.nba_decisions d USING (recommendation_id)"""
    q = lambda sql: con.execute(f"WITH b AS ({base}) {sql}", [start, end]).fetchall()  # noqa: E731
    accepted, overridden = q("""SELECT count(*) FILTER (WHERE agent = 'accepted'),
                                       count(*) FILTER (WHERE agent = 'overridden') FROM b""")[0]
    return {
        "recommendations": q("SELECT count(*) FROM b")[0][0],
        "agent_decisions": accepted + overridden,
        "override_rate": overridden / (accepted + overridden) if accepted + overridden else None,
        "overrides_by_reason": dict(q("""SELECT reason_code, count(*) FROM b WHERE agent = 'overridden'
                                         GROUP BY 1 ORDER BY 2 DESC""")),
        "overrides_by_action": dict(q("""SELECT action, count(*) FROM b WHERE agent = 'overridden'
                                         GROUP BY 1 ORDER BY 2 DESC""")),
        "safeguard_overrides": q(f"""SELECT recommendation_id, case_id, reason_code FROM b
                                     WHERE agent = 'overridden'
                                       AND reason_code IN ({", ".join(f"'{r}'" for r in SAFEGUARD_REASONS)})
                                     ORDER BY as_of_date, recommendation_id"""),
        "agents_by_override_rate": q("""
            SELECT agent_id, count(*) AS decided, avg(CAST(agent = 'overridden' AS INTEGER)) AS override_rate
            FROM b WHERE agent IN ('accepted', 'overridden') GROUP BY 1 HAVING count(*) >= 5
            ORDER BY override_rate DESC, decided DESC LIMIT 10"""),
        "rejections_by_reason": dict(q("""SELECT reason_code, count(*) FROM b WHERE approval = 'rejected'
                                          GROUP BY 1 ORDER BY 2 DESC""")),
        "still_pending": dict(q("""SELECT approval_reason, count(*) FROM b WHERE approval = 'pending'
                                   GROUP BY 1 ORDER BY 2 DESC""")),
    }


def main() -> int:
    from trustissues.common.db import connect

    ap = argparse.ArgumentParser(description="Review the NBA queue (gold.nba_overrides)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--date", type=date.fromisoformat, default=config.AS_OF_DATE)
    b = sub.add_parser("approve-batch")
    b.add_argument("--lead", required=True)
    b.add_argument("--date", type=date.fromisoformat, default=config.AS_OF_DATE)
    b.add_argument("--reject", nargs="*", default=[], metavar="REC=REASON")
    m = sub.add_parser("summary")
    m.add_argument("--from", dest="start", type=date.fromisoformat, default=config.AS_OF_DATE)
    m.add_argument("--to", dest="end", type=date.fromisoformat, default=config.AS_OF_DATE)
    args = ap.parse_args()

    if args.cmd == "approve-batch":
        con = connect()
        rejected = dict(r.split("=", 1) for r in args.reject)
        print(approve_batch(con, args.lead, args.date, rejected))
        return 0
    con = connect(read_only=True)
    if args.cmd == "status":
        print(status(con, args.date).aggregate("state, approval_reason, count(*) AS n").order("state, approval_reason"))
    else:
        for k, v in summary(con, args.start, args.end).items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
