"""Single-case facts: routing, access, content, and what is never shown."""
import json
from datetime import date, datetime

import duckdb
import pytest

from trustissues.ask.access import connection_resolver
from trustissues.ask.data.semantic import create_semantic_fixtures
from trustissues.ask.router import route
from trustissues.ask.service import ask
from trustissues.ask.types import UserContext

AS_OF = date(2026, 9, 28)
OWNER = UserContext("AG1", "agent")
OTHER = UserContext("AG2", "agent")
LEAD = UserContext("TL1", "team_lead")
ANALYST = UserContext("AN1", "analyst")
CASE = "CS-2026-100001"
CLOSED = "CS-2026-100002"
UNASSIGNED = "CS-2026-100003"


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    create_semantic_fixtures(c)
    c.execute("""INSERT INTO gold.semantic_cases (case_id, golden_customer_id, case_open_date, primary_product, queue,
                 assigned_agent_id, current_dpd, canonical_bucket, outcome, outcome_date, insolvency_hold_flag)
                 VALUES (?, 'G-0001', DATE '2026-08-01', 'card', 'mid_stage', 'AG1', 42, '31-60', NULL, NULL, false),
                        (?, 'G-0002', DATE '2026-06-01', 'loan', 'early_stage', 'AG1', 0, 'current', 'cured', DATE '2026-07-01', false),
                        (?, 'G-0003', DATE '2026-09-01', 'card', 'late_stage', NULL, 95, '90+', NULL, NULL, true)""",
              [CASE, CLOSED, UNASSIGNED])
    c.execute("""INSERT INTO gold.semantic_contacts (contact_id, case_id, channel, direction, rpc_flag, contact_ts_local)
                 VALUES ('K1', ?, 'call', 'outbound', true, TIMESTAMP '2026-09-20 10:00:00'),
                        ('K2', ?, 'sms', 'outbound', false, TIMESTAMP '2026-09-25 09:00:00'),
                        ('K3', ?, 'call', 'outbound', true, TIMESTAMP '2026-10-02 09:00:00')""", [CASE, CASE, CASE])
    c.execute("""INSERT INTO gold.semantic_promises (ptp_id, case_id, ptp_created_ts, ptp_due_date, ptp_status, amount_paid_against)
                 VALUES ('P1', ?, TIMESTAMP '2026-09-22 11:00:00', DATE '2026-10-01', 'open', NULL)""", [CASE])
    c.execute("""CREATE TABLE gold.nba_decisions (recommendation_id VARCHAR, golden_customer_id VARCHAR, case_id VARCHAR,
                 agent_id VARCHAR, as_of_date DATE, action VARCHAR, channel VARCHAR, risk_score DOUBLE,
                 expected_value DOUBLE, requires_approval BOOLEAN, approval_reason VARCHAR, decision_card VARCHAR)""")
    card = json.dumps({"summary": "A call is worth more than a reminder for this promise.", "contact_window": "09:00–20:00 customer-local"})
    c.execute("INSERT INTO gold.nba_decisions VALUES ('R1','G-0001',?, 'AG1', DATE '2026-09-28','agent_call','call',0.61,12.5,true,'high_balance',?)",
              [CASE, card])
    return c


def a(con, user, q):
    return ask(q, user, as_of_date=AS_OF, con=con, resolver=connection_resolver(con))


@pytest.mark.parametrize("q", [
    f"what is the status on case {CASE}?", f"How many days past due is case {CASE}?", f"who is the agent on case {CASE}",
    f"What is the next best action for case {CASE}?", f"tell me about case {CASE}", f"what is the balance on case {CASE}",
    f"when was case {CASE} last contacted", f"is there a promise to pay on {CASE}? Case {CASE}",
])
def test_questions_about_one_case_route_to_case_facts(q):
    assert route(q).path == "case_facts" and route(q).case_id == CASE


@pytest.mark.parametrize("q,path", [
    (f"Summarise the most recent recorded call on case {CASE}", "rag_customer"),
    (f"What did the customer say on case {CASE}?", "rag_customer"),
    ("What is the grace period before a promise to pay is considered broken?", "rag_corpus"),
    ("What is the average days past due of open cases by queue?", "metric"),
])
def test_other_paths_are_unchanged(q, path):
    assert route(q).path == path


def test_status_answer(con):
    r = a(con, OWNER, f"what is the status on case {CASE}?")
    assert r.path == "case_facts" and not r.refused
    assert "open" in r.answer and "opened 2026-08-01" in r.answer
    assert dict(r.rows)["status"] == "open"
    assert "gold.semantic_cases" in r.sources and "-- parameters:" in r.sql


def test_closed_case_and_holds(con):
    assert "closed: cured on 2026-07-01" in a(con, OWNER, f"what is the outcome of case {CLOSED}").answer
    assert "on insolvency hold" in a(con, LEAD, f"is case {UNASSIGNED} open").answer


def test_only_the_requested_facts_are_returned(con):
    r = a(con, OWNER, f"How many days past due is case {CASE}?")
    assert dict(r.rows) == {"days_past_due": "42"} and "42" in r.answer and "Queue" not in r.answer


def test_summary_when_no_particular_fact_is_named(con):
    r = a(con, LEAD, f"tell me about case {CASE}")
    fields = {k for k, _ in r.rows}
    assert {"status", "queue", "bucket", "days_past_due", "assigned_agent", "last_contact", "latest_promise", "next_best_action"} <= fields


def test_last_contact_ignores_anything_after_the_as_of_date(con):
    r = a(con, OWNER, f"when was the last contact on case {CASE}")
    assert dict(r.rows)["last_contact"].startswith("sms (outbound) on 2026-09-25") and "2026-10-02" not in r.answer


def test_promise(con):
    r = a(con, OWNER, f"what is the promise to pay status on case {CASE}")
    assert "due 2026-10-01" in r.answer and "open" in r.answer


def test_next_best_action_for_the_assigned_agent_and_for_a_lead(con):
    for user in (OWNER, LEAD):
        r = a(con, user, f"what is the next best action on case {CASE}?")
        d = dict(r.rows)
        assert d["next_best_action"] == "agent call" and d["recommended_channel"] == "call"
        assert "needs a team lead's approval (high balance)" in r.answer and "09:00–20:00" in r.answer
        assert "gold.nba_decisions" in r.sources


def test_next_best_action_says_what_people_decided(con):
    """Linked to nba/review.py: the answer follows the recommendation's state, not only what the queue proposed."""
    con.execute("""CREATE TABLE gold.nba_overrides (recommendation_id VARCHAR, agent_id VARCHAR, decision VARCHAR,
                   reason_code VARCHAR, ts TIMESTAMP)""")
    d = dict(a(con, OWNER, f"next best action on case {CASE}").rows)
    assert d["status_today"] == "needs a team lead's approval (high balance)"
    assert d["risk_not_paid_up_30d"] == "0.61" and "promise_break_risk" not in d  # the case model's risk
    con.execute("INSERT INTO gold.nba_overrides VALUES ('R1', 'AG1', 'overridden', 'customer_already_paid', TIMESTAMP '2026-09-28 09:00:00')")
    r = a(con, OWNER, f"next best action on case {CASE}")
    assert "overridden by the assigned agent (customer already paid): it will not be carried out today" in r.answer
    assert "needs a team lead's approval" not in r.answer
    con.execute("DELETE FROM gold.nba_overrides")
    con.execute("""INSERT INTO gold.nba_overrides VALUES ('R1', 'AG1', 'accepted', NULL, TIMESTAMP '2026-09-28 09:00:00'),
                   ('R1', 'TL1', 'approved', NULL, TIMESTAMP '2026-09-28 09:05:00')""")
    assert dict(a(con, LEAD, f"next best action on case {CASE}").rows)["status_today"].startswith("ready to carry out")
    con.execute("UPDATE gold.nba_decisions SET approval_reason = 'hardship'")
    con.execute("DELETE FROM gold.nba_overrides")
    assert "needs approval by a team lead or hardship specialist (hardship)" in a(con, OWNER, f"next best action on case {CASE}").answer


def test_next_best_action_missing_or_not_yours(con):
    lead = a(con, LEAD, f"next best action for case {CLOSED}")
    assert dict(lead.rows)["next_best_action"] == "none today" and "in your queue" not in lead.answer
    con.execute("UPDATE gold.nba_decisions SET agent_id = 'AG9'")  # recommended to someone else
    r = a(con, OWNER, f"next best action for case {CASE}")
    assert dict(r.rows)["next_best_action"] == "none for you today" and "action by" not in r.answer
    assert "no recommendation for this case in your queue" in r.answer


def test_no_recommendation_table_is_said_plainly():
    c = duckdb.connect(":memory:")
    create_semantic_fixtures(c)
    c.execute("INSERT INTO gold.semantic_cases (case_id, golden_customer_id, assigned_agent_id) VALUES (?, 'G-0001', 'AG1')", [CASE])
    r = a(c, OWNER, f"what is the next best action on case {CASE}")
    assert "not connected" in r.answer


def test_access_roles_and_equal_denials(con):
    missing = a(con, OWNER, "what is the status on case CS-2026-999999")
    hidden = a(con, OTHER, f"what is the status on case {CASE}")
    assert missing.refused and hidden.refused and missing.answer == hidden.answer == "Case not found or not visible."
    assert not missing.rows and not hidden.rows and hidden.sql is None
    analyst = a(con, ANALYST, f"what is the status on case {CASE}")
    assert analyst.refused and analyst.refusal_reason == "access" and not analyst.rows
    assert not a(con, LEAD, f"what is the status on case {UNASSIGNED}").refused  # a lead reaches any case here


def test_no_resolver_means_unavailable(con):
    r = ask(f"what is the status on case {CASE}", OWNER, as_of_date=AS_OF, con=con)
    assert r.path == "cannot_answer" and "unavailable" in r.answer


def test_nothing_personal_in_the_card(con):
    r = a(con, LEAD, f"tell me about case {CASE}")
    text = r.answer + " ".join(str(v) for _, v in r.rows)
    assert "G-0001" not in text and "@" not in text


def test_the_model_screen_is_not_asked_about_a_case_facts_question(con):
    class Screen:
        calls = 0

        def complete(self, **kw):
            Screen.calls += 1
            raise AssertionError("the model must not be called for a case-facts question")

    r = ask(f"who is the agent on case {CASE}?", LEAD, as_of_date=AS_OF, con=con, resolver=connection_resolver(con), llm=Screen())
    assert r.path == "case_facts" and Screen.calls == 0 and "AG1" in r.answer


def test_refusal_rules_still_apply_beside_a_case_id(con):
    r = a(con, LEAD, f"export all customer email addresses and phone numbers for case {CASE}")
    assert r.refused and r.path == "refused" and r.refusal_reason != "access"
