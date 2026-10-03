"""Data for the Agent Queue and Decision Card pages (app/pages). Plain functions, no Streamlit, so they are tested
like the rest of nba/. Reads gold.nba_decisions and gold.nba_overrides (written by nba/queue.py --write-gold and
nba/review.py) and the case's own history through features/sources.py (no PII, no protected attributes).

Who sees what (demo scope, from agents.role): an agent sees the recommendations assigned to them; team leads,
hardship specialists and QA analysts see every recommendation (they approve and review). Decisions go through
nba/review.py, which re-checks who may decide what.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from trustissues.features import sources
from trustissues.nba import review

SUPERVISORS = ("team_lead", "hardship_specialist", "qa_analyst")
# Queue order for an agent: support calls (duty of care) first, then calls, then other contact, then waits.
ACTION_ORDER = {"support_call": 0, "agent_call": 1, "sms_reminder": 2, "email_reminder": 2, "push_reminder": 2,
                "letter": 2, "wait": 3}


class AccessDenied(PermissionError):
    pass


def staff(con, user_id: str) -> dict | None:
    """The member of staff: role, team, certified, active. None if unknown."""
    row = con.execute(f"""SELECT agent_id, role, team_id, team_lead_id, coalesce(vulnerable_customer_certified, false),
                                 status FROM ({sources.sql('agents')}) WHERE agent_id = ?""", [user_id]).fetchone()
    if row is None:
        return None
    keys = ("agent_id", "role", "team_id", "team_lead_id", "certified", "status")
    return dict(zip(keys, row))


def staff_list(con, as_of: date) -> pd.DataFrame:
    """Active staff to act as in the demo: everyone with recommendations today, plus supervisors."""
    roles = ", ".join(f"'{r}'" for r in SUPERVISORS)
    return con.execute(f"""
        WITH work AS (SELECT agent_id, count(*) AS cases, count(*) FILTER (WHERE action IN ('agent_call', 'support_call'))
                             AS calls FROM gold.nba_decisions WHERE as_of_date = ? GROUP BY 1)
        SELECT a.agent_id, a.role, a.team_id, coalesce(w.cases, 0) AS cases, coalesce(w.calls, 0) AS calls
        FROM ({sources.sql('agents')}) a LEFT JOIN work w USING (agent_id)
        WHERE a.status = 'active' AND (w.cases > 0 OR a.role IN ({roles}))
        ORDER BY a.role = 'team_lead' DESC, w.calls DESC NULLS LAST, a.agent_id""", [as_of]).df()


def _state(con, as_of: date) -> pd.DataFrame:
    return review.status(con, as_of).select("recommendation_id, approval, agent, state").df()


def agent_queue(con, agent_id: str, as_of: date) -> pd.DataFrame:
    """The agent's recommendations for the day, in working order, with where each one stands. One row per customer
    (golden customer): a person with several cases is contacted on one of them at most (nba/queue.py), so the list
    shows that case, or the first in working order, and `other_cases` counts the rest (on the decision card)."""
    q = con.execute("""
        SELECT recommendation_id, case_id, golden_customer_id AS customer, action, channel, risk_score, expected_value,
               approval_reason, json_extract_string(decision_card, '$.summary') AS summary,
               json_extract_string(decision_card, '$.support_reason') AS support_reason
        FROM gold.nba_decisions WHERE as_of_date = ? AND agent_id = ?""", [as_of, agent_id]).df()
    q = q.merge(_state(con, as_of), on="recommendation_id", how="left")
    q["order"] = q["action"].map(ACTION_ORDER).fillna(9)
    q = q.sort_values(["order", "expected_value", "risk_score"], ascending=[True, False, False]).drop(columns="order")
    q["other_cases"] = q.groupby("customer")["recommendation_id"].transform("size") - 1
    return q.drop_duplicates("customer", keep="first")


def lead_digest(con, as_of: date) -> dict:
    """For a team lead: where the day's recommendations stand, the digital batch, and the approvals waiting."""
    st = review.status(con, as_of).df()
    pending = st[(st["approval"] == "pending") & (st["approval_reason"] != "digital_batch")]
    pending = pending.merge(con.execute("""
        SELECT recommendation_id, risk_score, json_extract_string(decision_card, '$.summary') AS summary
        FROM gold.nba_decisions WHERE as_of_date = ?""", [as_of]).df(), on="recommendation_id")
    return {
        "states": st["state"].value_counts().to_dict(),
        "batch_pending": int(((st["approval_reason"] == "digital_batch") & (st["approval"] == "pending")).sum()),
        "batch_total": int((st["approval_reason"] == "digital_batch").sum()),
        "pending": pending.sort_values(["approval_reason", "risk_score"], ascending=[True, False]),
    }


def can_see(user: dict | None, rec: dict) -> bool:
    return bool(user) and user["status"] == "active" and (user["role"] in SUPERVISORS or rec["agent_id"] == user["agent_id"])


def card(con, recommendation_id: str, user: dict | None) -> dict:
    """One recommendation, its decision card and where it stands. Raises AccessDenied if the user may not see it."""
    df = con.execute("SELECT * FROM gold.nba_decisions WHERE recommendation_id = ?", [recommendation_id]).df()
    if df.empty:
        raise KeyError(recommendation_id)
    rec = df.iloc[0].to_dict()
    if not can_see(user, rec):
        raise AccessDenied(f"{recommendation_id} is not assigned to {user['agent_id'] if user else 'you'}")
    rec["card"] = json.loads(rec.pop("decision_card"))
    st = _state(con, rec["as_of_date"])
    rec["status"] = st[st["recommendation_id"] == recommendation_id].iloc[0].to_dict()
    rec["history"] = con.execute("""SELECT ts, agent_id AS decided_by, decision, reason_code FROM gold.nba_overrides
                                    WHERE recommendation_id = ? ORDER BY ts""", [recommendation_id]).df()
    return rec


def customer_context(con, customer: str, as_of: date) -> dict:
    """The person behind a case (golden customer): all their cases, and their hardship offers across those cases,
    known before the as-of date. Sources must be registered."""
    cases = con.execute("""
        SELECT case_id, case_status AS status, primary_product AS product, strftime(case_open_date, '%Y-%m-%d') AS opened,
               current_dpd AS days_past_due, total_overdue, coalesce(hardship_flag, false) AS hardship
        FROM src_cases WHERE coalesce(golden_customer_id, crm_customer_id) = ? AND case_open_date < CAST(? AS DATE)
        ORDER BY case_status = 'open' DESC, case_open_date DESC""", [customer, as_of]).df()
    offers = con.execute("""
        SELECT h.offer_ts AS offered, h.case_id, h.program_id AS programme,
               CASE WHEN h.response_ts < CAST(? AS TIMESTAMP) THEN h.customer_response END AS response,
               CASE WHEN h.response_ts < CAST(? AS TIMESTAMP) THEN h.plan_status END AS plan_status
        FROM src_hardship_offers h JOIN src_cases c USING (case_id)
        WHERE coalesce(c.golden_customer_id, c.crm_customer_id) = ? AND h.offer_ts < CAST(? AS TIMESTAMP)
        ORDER BY h.offer_ts DESC LIMIT 10""", [as_of, as_of, customer, as_of]).df()
    return {"customer": customer, "cases": cases, "offers": offers}


def case_context(con, case_id: str, as_of: date) -> dict:
    """The case as the agent needs it: facts, recent contacts and promises, known before the as-of date."""
    facts = con.execute("""
        SELECT case_id, primary_product AS product, strftime(case_open_date, '%Y-%m-%d') AS opened,
               current_dpd AS days_past_due, total_overdue, current_bucket AS bucket, test_cell, coalesce(hardship_flag, false) AS hardship,
               coalesce(vulnerable_customer_flag, false) AS vulnerable, coalesce(dispute_flag, false) AS dispute,
               contacts_last_7d, contact_cap_7d
        FROM src_cases WHERE case_id = ?""", [case_id]).df()
    contacts = con.execute("""
        SELECT contact_ts AS "when", channel, direction, treatment_code AS treatment, rpc_flag AS reached,
               ptp_made_flag AS promise_made, payment_taken_flag AS payment_taken
        FROM src_contacts WHERE case_id = ? AND contact_ts < CAST(? AS TIMESTAMP)
        ORDER BY contact_ts DESC LIMIT 10""", [case_id, as_of]).df()
    promises = con.execute("""
        SELECT ptp_created_ts AS made, ptp_amount AS amount, ptp_to_overdue_ratio AS share_of_overdue,
               ptp_due_date AS due, ptp_status AS status
        FROM src_promises WHERE case_id = ? AND ptp_created_ts < CAST(? AS TIMESTAMP)
        ORDER BY ptp_created_ts DESC LIMIT 5""", [case_id, as_of]).df()
    return {"facts": facts.iloc[0].to_dict() if len(facts) else {}, "contacts": contacts, "promises": promises}
