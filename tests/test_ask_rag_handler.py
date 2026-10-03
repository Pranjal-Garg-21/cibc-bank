from datetime import date

import pytest

from trustissues.common.interfaces import RagRequest, RagResponse
from trustissues.common.llm import LLMError
from trustissues.ask import rag_handler
from trustissues.ask.access import NOT_VISIBLE, AccessDenied, case_customer_resolver
from trustissues.ask.rag import answer as policy_answer
from trustissues.ask.rag import customer_path
from trustissues.ask.service import ask
from trustissues.ask.types import UserContext

# P4's customer-path fixture: G-1 holds case CS-2026-000001, assigned to AG-NOW (team lead TL-1) on 2026-09-28
from test_customer_path import AS_OF, CASE, con, make_llm  # noqa: F401  (con is a fixture)


def req(kind="rag_corpus"):
    return RagRequest("Does the DNCL stop collection calls?", kind, "team_lead", "lead1", date(2026, 9, 28))


def test_corpus_goes_to_policy_path(monkeypatch):
    seen = {}
    def fake(question, index, **kw):
        seen.update(question=question, index=index, **kw)
        return RagResponse("No.", evidence=["q"])
    monkeypatch.setattr(policy_answer, "answer_policy", fake)
    out = rag_handler.make_rag_handler("IDX", llm="LLM")(req())
    assert out.answer == "No." and seen["index"] == "IDX"
    assert (seen["as_of"], seen["user_role"], seen["user_id"]) == (date(2026, 9, 28), "team_lead", "lead1")


def test_model_outage_is_not_found_not_invented(monkeypatch):
    def boom(*a, **k):
        raise LLMError("down")
    monkeypatch.setattr(policy_answer, "answer_policy", boom)
    out = rag_handler.make_rag_handler("IDX", llm="LLM")(req())
    assert out.not_found and out.answer is None and "unavailable" in out.coverage


def test_customer_path_without_a_warehouse_says_so():
    out = rag_handler.make_rag_handler("IDX", llm="LLM")(req("rag_customer"))
    assert out.not_found and "not connected" in out.coverage


def test_customer_goes_to_p4_customer_path_with_the_case(monkeypatch):
    seen = {}
    def fake(r, *, con, llm):
        seen.update(req=r, con=con, llm=llm)
        return RagResponse("Promised $95.")
    monkeypatch.setattr(customer_path, "answer_customer", fake)
    r = RagRequest("What was agreed?", "rag_customer", "agent", "AG-1", date(2026, 9, 28), "G-1", case_id="CS-1")
    out = rag_handler.make_rag_handler("IDX", llm="LLM", con="CON")(r)
    assert out.answer == "Promised $95." and seen["req"] is r and (seen["con"], seen["llm"]) == ("CON", "LLM")


def test_customer_path_refusal_uses_the_routers_wording(monkeypatch):
    def deny(r, **kw):
        raise customer_path.CustomerAccessDenied(customer_path.DENIED)
    monkeypatch.setattr(customer_path, "answer_customer", deny)
    with pytest.raises(AccessDenied, match=NOT_VISIBLE):
        rag_handler.make_rag_handler("IDX", llm="LLM", con="CON")(req("rag_customer"))


Q = "Summarise the most recent call on case {}: what did the customer agree to?"


def test_ask_answers_a_case_question_from_the_customer_history(con, tmp_path):
    """End to end: router -> case resolver (gold.case_customer + assignment) -> P4 customer path -> answer card."""
    reply = {"answer": "Promised $95 on 14 Oct.", "not_found": False, "confidence": 0.9, "conflict_note": None,
             "evidence": [{"source": "S4", "quote": "inbound | Promise to pay | promise made"}]}
    llm, be = make_llm(tmp_path, reply)
    card = ask(Q.format(CASE), UserContext("AG-NOW", "agent"), as_of_date=AS_OF,
               rag=rag_handler.make_rag_handler("IDX", llm, con=con), resolver=case_customer_resolver(con, AS_OF))
    assert card.path == "rag_customer" and not card.refused
    assert card.answer == "Promised $95 on 14 Oct." and card.sources == ["contact:C-9"]
    assert f"The question is about case {CASE}; only that case's items are listed." in be.calls[0]["user"]
    assert "OLD-CASE" not in be.calls[0]["user"]  # the customer's other case is not read (P2's spec)


@pytest.mark.parametrize("user, case", [
    (UserContext("AG-OLD", "agent"), CASE),            # router: case reassigned before the as-of date
    (UserContext("TL-2", "team_lead"), CASE),          # customer path: not this lead's team
    (UserContext("TL-2", "team_lead"), "CS-2026-999999"),  # no such case
])
def test_ask_refuses_every_hidden_case_the_same_way(con, tmp_path, user, case):
    llm, be = make_llm(tmp_path)
    card = ask(Q.format(case), user, as_of_date=AS_OF, rag=rag_handler.make_rag_handler("IDX", llm, con=con),
               resolver=case_customer_resolver(con, AS_OF))
    assert card.refused and card.refusal_reason == "access" and card.answer == NOT_VISIBLE and be.calls == []


def test_case_customer_resolver_reads_the_assignment_on_the_as_of_date(con):
    owner = case_customer_resolver(con, AS_OF)(CASE)
    assert (owner.golden_customer_id, owner.assigned_agent_id) == ("G-1", "AG-NOW")
    assert case_customer_resolver(con, date(2026, 9, 10))(CASE).assigned_agent_id == "AG-OLD"
    assert case_customer_resolver(con, AS_OF)("CS-2026-999999") is None
