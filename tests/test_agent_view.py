"""nba/agent_view.py: what the Agent Queue and Decision Card pages show, and who may see it."""
from datetime import date, datetime

import duckdb
import pytest

from trustissues.common.interfaces import DecisionCard, apply_stubs
from trustissues.features import sources
from trustissues.nba import agent_view, review

D = date(2026, 9, 28)
RECS = [  # recommendation_id, case, agent, action, value, requires_approval, approval_reason
    ("R-WAIT", "CS-1", "AG-1", "wait", 0.0, False, None),
    ("R-EMAIL", "CS-2", "AG-1", "email_reminder", 5.0, False, None),
    ("R-CALL", "CS-3", "AG-1", "agent_call", 9.0, False, None),
    ("R-SUP", "CS-4", "AG-1", "support_call", 0.0, True, "hardship"),
    ("R-OTHER", "CS-5", "AG-2", "agent_call", 7.0, False, None),
    ("R-SMS", "CS-6", None, "sms_reminder", 3.0, True, "digital_batch"),
    ("R-SAME", "CS-7", "AG-1", "wait", 0.0, False, None),  # the same person as CS-3: contacted there today
]
CUSTOMER = {"CS-7": "G-CS-3"}  # everyone else: G-<case>


@pytest.fixture
def con():
    c = duckdb.connect()
    apply_stubs(c)
    sources.create_empty_tables(c)
    for a, role in (("AG-1", "agent"), ("AG-2", "agent"), ("TL-1", "team_lead")):
        c.execute(f"INSERT INTO {sources.SCHEMA}.agents (agent_id, role, status, team_id) VALUES (?, ?, 'active', 'T1')",
                  [a, role])
    for rec, case, agent, action, value, needs, why in RECS:
        card = DecisionCard(recommended_action=action, channel=None, risk_score=0.5, summary=f"summary {rec}")
        c.execute("INSERT INTO gold.nba_decisions VALUES (?, ?, ?, ?, ?, ?, NULL, 0.5, ?, ?, ?, ?, 'm', 's', ?)",
                  [rec, CUSTOMER.get(case, f"G-{case}"), case, agent, D, action, value, needs, why, card.to_json(),
                   datetime(2026, 9, 28, 6)])
    c.execute(f"""INSERT INTO {sources.SCHEMA}.collections_cases (case_id, coll_customer_ref, case_status, primary_product,
                  current_dpd, total_overdue, case_open_date) VALUES ('CS-3', 'C-1', 'open', 'card', '40', '512.5',
                  '2026-08-01')""")
    for i, ts in enumerate(("2026-09-20 10:00:00", "2026-09-27 10:00:00", "2026-09-28 10:00:00")):
        c.execute(f"""INSERT INTO {sources.SCHEMA}.contact_history (contact_id, case_id, contact_ts_local, channel_resolved)
                      VALUES (?, 'CS-3', ?, 'call')""", [f"K{i}", ts])
    sources.register(c)
    return c


def test_agent_queue_order_and_scope(con):
    q = agent_view.agent_queue(con, "AG-1", D)
    assert list(q["recommendation_id"]) == ["R-SUP", "R-CALL", "R-EMAIL", "R-WAIT"]  # support, call, contact, wait
    assert dict(zip(q["recommendation_id"], q["other_cases"]))["R-CALL"] == 1  # R-SAME: same person, not listed
    assert dict(zip(q["recommendation_id"], q["state"])) == {"R-SUP": "waiting_approval", "R-CALL": "waiting_agent",
                                                            "R-EMAIL": "ready", "R-WAIT": "ready"}


def test_who_may_see_a_card(con):
    agent, other, lead = (agent_view.staff(con, a) for a in ("AG-1", "AG-2", "TL-1"))
    assert agent_view.card(con, "R-CALL", agent)["card"]["summary"] == "summary R-CALL"
    with pytest.raises(agent_view.AccessDenied):
        agent_view.card(con, "R-CALL", other)
    assert agent_view.card(con, "R-CALL", lead)["status"]["state"] == "waiting_agent"
    with pytest.raises(KeyError):
        agent_view.card(con, "R-NOPE", lead)


def test_card_shows_the_decisions_so_far(con):
    review.record(con, "R-CALL", "AG-1", "accepted", ts=datetime(2026, 9, 28, 9))
    rec = agent_view.card(con, "R-CALL", agent_view.staff(con, "AG-1"))
    assert rec["status"]["state"] == "ready" and list(rec["history"]["decision"]) == ["accepted"]


def test_lead_digest(con):
    d = agent_view.lead_digest(con, D)
    assert (d["batch_total"], d["batch_pending"]) == (1, 1)
    assert list(d["pending"]["recommendation_id"]) == ["R-SUP"]  # individual approvals only, not the batch
    review.approve_batch(con, "TL-1", D, ts=datetime(2026, 9, 28, 7))
    assert agent_view.lead_digest(con, D)["batch_pending"] == 0
    review.record(con, "R-SUP", "AG-1", "overridden", "hardship_disclosed", ts=datetime(2026, 9, 28, 8))
    assert agent_view.lead_digest(con, D)["pending"].empty  # overridden by its agent: nothing left to approve


def test_case_context_stops_at_the_as_of_date(con):
    ctx = agent_view.case_context(con, "CS-3", D)
    assert ctx["facts"]["product"] == "card" and ctx["facts"]["days_past_due"] == 40
    assert len(ctx["contacts"]) == 2  # the contact on the as-of date itself is not shown
    assert list(ctx["contacts"]["channel"]) == ["call", "call"]
    assert ctx["facts"]["opened"] == "2026-08-01"


def test_customer_context_lists_the_persons_cases_and_offers(con):
    con.execute(f"""INSERT INTO {sources.SCHEMA}.collections_cases (case_id, coll_customer_ref, case_status,
                    primary_product, case_open_date) VALUES ('CS-8', 'C-1', 'closed', 'loan', '2025-01-10')""")
    con.execute("INSERT INTO gold.case_customer (case_id, golden_customer_id) VALUES ('CS-3', 'G-1'), ('CS-8', 'G-1')")
    con.execute(f"""INSERT INTO {sources.SCHEMA}.offers (offer_id, case_id, program_id, offer_ts, customer_response,
                    response_ts) VALUES ('O-1', 'CS-8', 'HP-LOAN-SKIP', '2025-03-01 10:00:00', 'accepted',
                    '2025-03-02 10:00:00'), ('O-2', 'CS-8', 'HP-LOAN-RED3', '2026-10-01 10:00:00', NULL, NULL)""")
    who = agent_view.customer_context(con, "G-1", D)
    assert list(who["cases"]["case_id"]) == ["CS-3", "CS-8"]  # open first
    assert list(who["offers"]["programme"]) == ["HP-LOAN-SKIP"]  # the offer after the as-of date is not shown
