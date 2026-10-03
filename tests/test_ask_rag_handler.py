from datetime import date

from trustissues.common.interfaces import RagRequest, RagResponse
from trustissues.common.llm import LLMError
from trustissues.ask import rag_handler
from trustissues.ask.rag import answer as policy_answer


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


def test_customer_path_not_connected():
    out = rag_handler.make_rag_handler("IDX", llm="LLM")(req("rag_customer"))
    assert out.not_found
