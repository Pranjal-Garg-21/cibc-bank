"""Customer path (long context). A scripted stand-in replaces the model: these tests check access, point-in-time
filtering, the token budget, coverage and the answer checks, not the model's writing."""
import json
from datetime import date, datetime

import duckdb
import pytest

from trustissues.ask.rag import customer_path as cp
from trustissues.ask.rag.customer_path import (CustomerAccessDenied, TimelineItem, answer_customer, can_view_customer,
                                               describe_coverage, fetch_history, run_customer, select_within_budget)
from trustissues.common.audit import AuditLog
from trustissues.common.fixtures import load_fixtures
from trustissues.common.interfaces import RagRequest
from trustissues.common.llm import LLM, ResponseCache

AS_OF = date(2026, 9, 28)
CASE = "CS-2026-000001"
CALL = ("agent: Thank you for confirming.\ncustomer: I've been off work sick since July.\n"
        "customer: I can pay $95 Oct 14.\nagent: Just to confirm, a payment of $95.00 on October 14.")


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    for s in ("gold", "curated"):
        c.execute(f"CREATE SCHEMA {s}")
    c.execute("""CREATE TABLE gold.customer_timeline (golden_customer_id VARCHAR, event_ts TIMESTAMP, event_type VARCHAR,
                 source_table VARCHAR, source_id VARCHAR, account_id VARCHAR, agent_id VARCHAR, channel VARCHAR,
                 text VARCHAR, voice_sample_id VARCHAR, case_id VARCHAR, summary VARCHAR)""")
    rows = [
        ("G-1", "2024-09-15 16:52:00", "note", "N-1", None, "old case: customer moved, call 416-555-0199", "OLD-CASE", "call_note"),
        ("G-1", "2026-09-01 11:00:00", "note", "N-2", None, "money is tight, asked about hardship options", CASE, "call_note"),
        ("G-1", "2026-09-02 10:00:00", "note", "N-3", None, None, CASE, "call_note"),  # not eligible: no text
        ("G-1", "2026-09-10 09:00:00", "promise", "PTP-1", "phone", None, CASE, "promised 37 by 2026-09-22 | broken"),
        ("G-1", "2026-09-27 16:59:55", "transcript", "5010384", "call", CALL, CASE, "inbound call, 148 s, en-CA"),
        ("G-1", "2026-09-27 17:05:00", "contact", "C-9", "phone", None, CASE, "inbound | Promise to pay | promise made"),
        ("G-1", "2026-09-29 09:00:00", "note", "N-FUTURE", None, "customer paid in full", CASE, "call_note"),  # after as-of
        ("G-2", "2026-09-20 10:00:00", "note", "N-OTHER", None, "someone else's note", "CS-OTHER", "call_note"),
    ]
    for g, ts, et, sid, ch, text, case, summ in rows:
        c.execute("INSERT INTO gold.customer_timeline VALUES (?, ?, ?, NULL, ?, NULL, NULL, ?, ?, NULL, ?, ?)",
                  [g, ts, et, sid, ch, text, case, summ])
    c.execute("CREATE TABLE gold.case_customer (case_id VARCHAR, golden_customer_id VARCHAR)")
    c.execute(f"INSERT INTO gold.case_customer VALUES ('{CASE}', 'G-1'), ('OLD-CASE', 'G-1'), ('CS-OTHER', 'G-2')")
    c.execute("""CREATE TABLE curated.case_assignment_history (case_id VARCHAR, agent_id VARCHAR,
                 assigned_ts TIMESTAMP, unassigned_ts TIMESTAMP)""")
    c.execute(f"""INSERT INTO curated.case_assignment_history VALUES
        ('{CASE}', 'AG-OLD', '2026-08-27 08:00', '2026-09-26 08:00'),     -- reassigned before the as-of date
        ('{CASE}', 'AG-NOW', '2026-09-26 08:38', NULL),
        ('CS-OTHER', 'AG-OTHER', '2026-09-01 08:00', NULL),
        ('OLD-CASE', 'AG-LATER', '2026-10-01 08:00', NULL)""")           # assigned after the as-of date
    c.execute("CREATE TABLE curated.agents (agent_id VARCHAR, team_lead_id VARCHAR)")
    c.execute("INSERT INTO curated.agents VALUES ('AG-NOW', 'TL-1'), ('AG-OTHER', 'TL-2'), ('AG-OLD', 'TL-2')")
    c.execute("""CREATE TABLE curated.collections_cases (case_id VARCHAR, case_status VARCHAR, case_status_date DATE,
                 case_open_date DATE)""")
    c.execute(f"""INSERT INTO curated.collections_cases VALUES ('{CASE}', 'open', '2026-08-27', '2026-08-27'),
        ('OLD-CASE', 'cured', '2024-10-01', '2024-09-01'), ('CS-OTHER', 'open', '2026-09-01', '2026-09-01')""")
    return c


class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        return r if isinstance(r, str) else json.dumps(r)


def make_llm(tmp_path, *replies, audit=None):
    be = Scripted(*replies)
    return LLM(be, audit=audit, cache=ResponseCache(tmp_path / "c.sqlite"), models={"answer": "m", "bulk": "m"},
               max_retries=0, sleep=lambda s: None), be


# ---- 1 access ----------------------------------------------------------------------------------
@pytest.mark.parametrize("role, uid, ok", [
    ("agent", "AG-NOW", True),        # assigned today
    ("agent", "AG-OLD", False),       # assignment ended before the as-of date
    ("agent", "AG-LATER", False),     # assignment starts after the as-of date
    ("agent", "AG-OTHER", False),     # someone else's customer
    ("team_lead", "TL-1", True),      # lead of the assigned agent
    ("team_lead", "TL-2", False),     # lead of an agent who no longer has the case
    ("analyst", "AN-1", False),       # analysts never read customer text
    ("agent", None, False),
])
def test_access_follows_the_assignment_on_the_as_of_date(con, role, uid, ok):
    assert can_view_customer(con, user_role=role, user_id=uid, golden_customer_id="G-1", as_of=AS_OF) is ok


def test_access_is_judged_on_the_as_of_date_not_today(con):
    assert can_view_customer(con, user_role="agent", user_id="AG-OLD", golden_customer_id="G-1", as_of=date(2026, 9, 10))
    assert not can_view_customer(con, user_role="agent", user_id="AG-NOW", golden_customer_id="G-1", as_of=date(2026, 9, 10))


def test_team_leads_may_read_a_customer_whose_open_case_nobody_holds(con):
    con.execute("INSERT INTO gold.case_customer VALUES ('CS-NEW', 'G-3')")
    con.execute("INSERT INTO curated.collections_cases VALUES ('CS-NEW', 'open', '2026-09-24', '2026-09-24')")
    assert can_view_customer(con, user_role="team_lead", user_id="TL-9", golden_customer_id="G-3", as_of=AS_OF)
    assert not can_view_customer(con, user_role="agent", user_id="AG-NOW", golden_customer_id="G-3", as_of=AS_OF)
    # not yet open on an earlier date; and a held case (G-1's) is not open to every lead
    assert not can_view_customer(con, user_role="team_lead", user_id="TL-9", golden_customer_id="G-3", as_of=date(2026, 9, 20))
    assert not can_view_customer(con, user_role="team_lead", user_id="TL-9", golden_customer_id="G-1", as_of=AS_OF)


def test_missing_assignment_tables_mean_no_access():
    c = duckdb.connect(":memory:")
    assert not can_view_customer(c, user_role="agent", user_id="AG-NOW", golden_customer_id="G-1", as_of=AS_OF)


def test_refusal_never_calls_the_model_and_does_not_say_whether_the_customer_exists(con, tmp_path):
    llm, be = make_llm(tmp_path)
    msgs = []
    for gid in ("G-1", "G-DOES-NOT-EXIST"):
        with pytest.raises(CustomerAccessDenied) as e:
            run_customer("anything", gid, con=con, llm=llm, as_of=AS_OF, user_role="agent", user_id="AG-OTHER")
        msgs.append(str(e.value))
    assert msgs[0] == msgs[1] and not be.calls


# ---- 2-3 fetch and filter ----------------------------------------------------------------------
def test_history_is_point_in_time_and_one_customer_only(con):
    items, withheld = fetch_history(con, "G-1", AS_OF)
    ids = [i.source_id for i in items]
    assert ids == ["N-1", "N-2", "PTP-1", "5010384", "C-9"]  # oldest first; N-FUTURE and G-2's note excluded
    assert withheld == 1  # N-3 has no eligible text: withheld, never sent empty


def test_text_is_scrubbed_again_before_it_can_leave(con):
    items, _ = fetch_history(con, "G-1", AS_OF, scrub_text=True)
    assert "416-555-0199" not in items[0].text and "[PHONE]" in items[0].text


def test_a_past_question_does_not_see_outcomes_recorded_later(con):
    now, _ = fetch_history(con, "G-1", AS_OF)
    past, _ = fetch_history(con, "G-1", date(2026, 9, 15))
    assert next(i for i in now if i.source_id == "PTP-1").text.endswith("| broken")
    assert next(i for i in past if i.source_id == "PTP-1").text == "promised 37 by 2026-09-22"
    assert "5010384" not in [i.source_id for i in past]


def test_works_on_the_stub_shaped_timeline_without_case_or_summary_columns():
    c = duckdb.connect(":memory:")
    load_fixtures(c)
    items, _ = fetch_history(c, "G-000001", AS_OF)
    assert [i.source_id for i in items] == ["NOTE-9"]  # the promise row has no summary in the stub, so nothing to read
    assert items[0].case_id is None


# ---- 4 budget ----------------------------------------------------------------------------------
def _item(sid, day, n_chars, case=None):
    return TimelineItem("note", sid, datetime(2026, 9, day), "x" * n_chars, case)


def test_budget_keeps_newest_first_then_returns_oldest_first():
    items = [_item("a", 1, 340), _item("b", 2, 340), _item("c", 3, 340)]  # 115 tokens each at 3.5 chars/token
    chosen = select_within_budget(items, budget=240, chars_per_token=3.5)
    assert [i.source_id for i in chosen] == ["b", "c"]


def test_budget_reads_the_asked_about_case_first():
    items = [_item("case-old", 1, 340, CASE), _item("other-new", 3, 340, "X")]
    chosen = select_within_budget(items, budget=120, chars_per_token=3.5, case_id=CASE)
    assert [i.source_id for i in chosen] == ["case-old"]


def test_coverage_states_the_period_and_what_was_left_out():
    chosen = [_item("b", 2, 10, CASE), _item("c", 3, 10)]
    s = describe_coverage(chosen, total=3, withheld=1, as_of=AS_OF, case_id=CASE)
    assert "2026-09-02 to 2026-09-03" in s and "1 on case" in s and "1 items were left out" in s and "1 notes or calls" in s
    assert describe_coverage([], 0, 0, AS_OF) == "No history for this customer on or before 2026-09-28."


# ---- 5 end to end ------------------------------------------------------------------------------
def test_answers_from_the_call_with_checked_quotes_and_citations(con, tmp_path):
    reply = {"answer": "Off work sick since July; promised $95 on 14 Oct.", "not_found": False, "confidence": 0.9,
             "conflict_note": None,
             "evidence": [{"source": "S4", "quote": "customer: I can pay $95 Oct 14."},
                          {"source": "S4", "quote": "customer: I won the lottery."}]}  # invented: dropped
    llm, be = make_llm(tmp_path, reply, audit=AuditLog(con))
    run = run_customer("Summarise the most recent recorded call: what did the customer say and agree to?", "G-1",
                       con=con, llm=llm, as_of=AS_OF, user_role="agent", user_id="AG-NOW", case_id=CASE)
    r = run.response
    assert not r.not_found and r.evidence == ["customer: I can pay $95 Oct 14."]
    assert [(c.kind, c.source_id, c.source_ts) for c in r.citations] == [("transcript", "5010384", "2026-09-27 16:59")]
    assert "on case CS-2026-000001" in r.coverage

    prompt = be.calls[0]["user"]
    assert "[S4] transcript | 5010384 | 2026-09-27 16:59 | case CS-2026-000001" in prompt
    assert "The question is about case CS-2026-000001" in prompt and "N-FUTURE" not in prompt
    kind, version = con.execute("SELECT kind, prompt_version FROM gold.audit_log").fetchone()
    assert (kind, version) == ("rag_customer", cp.PROMPT_VERSION)


def test_an_answer_with_no_real_quote_is_not_found(con, tmp_path):
    reply = {"answer": "Paid in full.", "not_found": False, "confidence": 0.8, "conflict_note": None,
             "evidence": [{"source": "S1", "quote": "customer paid in full"}]}
    llm, _ = make_llm(tmp_path, reply)
    r = run_customer("Did they pay?", "G-1", con=con, llm=llm, as_of=AS_OF, user_role="agent", user_id="AG-NOW").response
    assert r.not_found and r.answer.startswith("Not found in this customer's history")


def test_no_history_means_no_model_call(con, tmp_path):
    con.execute("INSERT INTO gold.case_customer VALUES ('CS-EMPTY', 'G-EMPTY')")
    con.execute("INSERT INTO curated.case_assignment_history VALUES ('CS-EMPTY', 'AG-NOW', '2026-09-01', NULL)")
    llm, be = make_llm(tmp_path)
    r = run_customer("Anything?", "G-EMPTY", con=con, llm=llm, as_of=AS_OF, user_role="agent", user_id="AG-NOW").response
    assert r.not_found and not be.calls and r.coverage.startswith("No history")


def test_french_question_gets_the_french_not_found(con, tmp_path):
    llm, _ = make_llm(tmp_path, {"answer": "", "not_found": True, "evidence": [], "confidence": 1, "conflict_note": None})
    r = run_customer("Quel est le dernier paiement du client?", "G-1", con=con, llm=llm, as_of=AS_OF,
                     user_role="agent", user_id="AG-NOW").response
    assert r.not_found and r.answer.startswith("Introuvable dans l'historique")


def test_i6_entry_point(con, tmp_path):
    reply = {"answer": "Promised $95 on 14 Oct.", "not_found": False, "confidence": 0.9, "conflict_note": None,
             "evidence": [{"source": "S5", "quote": "inbound | Promise to pay | promise made"}]}
    llm, _ = make_llm(tmp_path, reply)
    req = RagRequest("What was agreed?", "rag_customer", "agent", "AG-NOW", AS_OF, "G-1", case_id=CASE)
    r = answer_customer(req, con=con, llm=llm)
    assert r.citations[0].kind == "contact" and r.citations[0].source_id == "C-9"
    with pytest.raises(PermissionError):
        answer_customer(RagRequest("x", "rag_customer", "agent", "AG-NOW", AS_OF), con=con, llm=llm)
