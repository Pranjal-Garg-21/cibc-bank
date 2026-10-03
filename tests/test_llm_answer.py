"""LLM wrapper, audit log and answer guarantees. A scripted stand-in replaces the model: these tests check our
code around the model (retries, cache, audit, quote checking, not_found), not the model's writing."""
import json
from datetime import date

import duckdb
import pytest

from trustissues.ask.rag.answer import Source, answer_question, policy_sources
from trustissues.common.audit import AuditLog
from trustissues.common.llm import LLM, LLMError, LLMOutputError, ResponseCache, parse_json

MODELS = {"answer": "m-answer", "bulk": "m-bulk"}


class Scripted:
    """Returns queued replies; an Exception in the queue is raised instead."""
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


def make(tmp_path, *replies, audit=None, retries=2):
    be = Scripted(*replies)
    return LLM(be, audit=audit, cache=ResponseCache(tmp_path / "c.sqlite"), models=MODELS,
               max_retries=retries, sleep=lambda s: None), be


def call(llm, **kw):
    return llm.complete(system="s", user="u", prompt_version="v1", **kw)


# ---- llm.py ------------------------------------------------------------------------------------
def test_json_is_parsed_through_fences_and_chatter():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": 1} Hope that helps') == {"a": 1}
    with pytest.raises(LLMOutputError):
        parse_json("no json here")


def test_retries_then_succeeds_and_uses_the_tier_model(tmp_path):
    llm, be = make(tmp_path, RuntimeError("429"), RuntimeError("503"), "ok")
    r = call(llm, tier="bulk")
    assert r.text == "ok" and len(be.calls) == 3 and be.calls[0]["model"] == "m-bulk"


def test_gives_up_after_the_retry_limit(tmp_path):
    llm, be = make(tmp_path, *[RuntimeError("down")] * 3)
    with pytest.raises(LLMError):
        call(llm)
    assert len(be.calls) == 3


def test_identical_requests_hit_the_cache_and_changed_ones_do_not(tmp_path):
    llm, be = make(tmp_path, "first", "second")
    assert not call(llm).cached and call(llm).cached and len(be.calls) == 1
    assert llm.complete(system="s", user="u", prompt_version="v2").text == "second"  # new prompt version = new key


def test_bad_json_is_retried_and_never_cached(tmp_path):
    llm, be = make(tmp_path, "garbage", {"a": 1})
    assert call(llm, json_output=True).data == {"a": 1} and len(be.calls) == 2  # recovered on the second try
    assert call(llm, json_output=True).cached  # only the good reply was stored


def test_persistent_bad_json_raises_after_the_retry_limit(tmp_path):
    llm, be = make(tmp_path, *["garbage"] * 3)
    with pytest.raises(LLMOutputError):
        call(llm, json_output=True)
    assert len(be.calls) == 3


# ---- audit.py ----------------------------------------------------------------------------------
def test_every_call_is_audited_including_cached_ones(tmp_path):
    con = duckdb.connect(":memory:")
    llm, _ = make(tmp_path, "hello", audit=AuditLog(con))
    ctx = {"kind": "rag", "input_text": "q?", "user_role": "agent", "user_id": "A1", "sources": ["policy | X"]}
    call(llm, audit_ctx=ctx)
    call(llm, audit_ctx=ctx)  # second is a cache hit but is still logged
    rows = con.execute("SELECT kind, input_text, answer, model, prompt_version, user_id FROM gold.audit_log").fetchall()
    assert rows == [("rag", "q?", "hello", "m-answer", "v1", "A1")] * 2
    assert "[system]" in con.execute("SELECT prompt FROM gold.audit_log").fetchone()[0]


# ---- answer.py ---------------------------------------------------------------------------------
TEXT = "A maximum of **3 outbound call attempts** in any rolling 7 days per case."
SRC = [Source("policy", "POL-COLL-001", TEXT, "3.0", "§5.2"),
       Source("policy", "FAQ-COLL-001", "Two business days after the promised date.", "3.2", "Q4")]
ASK = dict(as_of=date(2026, 9, 28), user_role="agent")


def reply(**kw):
    base = {"answer": "3 attempts per rolling 7 days.", "evidence": [{"source": "S1", "quote": "3 outbound call attempts"}],
            "not_found": False, "confidence": 0.9, "conflict_note": None}
    return {**base, **kw}


def test_supported_answer_returns_quote_and_citation(tmp_path):
    llm, _ = make(tmp_path, reply())
    r = answer_question("How many calls?", SRC, llm=llm, **ASK)
    c = r.citations[0]
    assert not r.not_found and r.evidence == ["3 outbound call attempts"]
    assert (c.kind, c.source_id, c.version, c.section) == ("policy", "POL-COLL-001", "3.0", "§5.2")
    assert r.confidence == 0.9


def test_no_sources_means_not_found_and_no_llm_call(tmp_path):
    llm, be = make(tmp_path)
    r = answer_question("Anything?", [], llm=llm, **ASK)
    assert r.not_found and be.calls == []


def test_invented_quote_is_dropped_and_unsupported_answer_becomes_not_found(tmp_path):
    llm, _ = make(tmp_path, reply(evidence=[{"source": "S1", "quote": "5 attempts per week"}]))
    assert answer_question("q", SRC, llm=llm, **ASK).not_found


def test_only_valid_quotes_survive_and_only_their_sources_are_cited(tmp_path):
    ev = [{"source": "S1", "quote": "3 outbound call attempts"}, {"source": "S2", "quote": "made up"},
          {"source": "S9", "quote": "x"}]
    llm, _ = make(tmp_path, reply(evidence=ev))
    r = answer_question("q", SRC, llm=llm, **ASK)
    assert r.evidence == ["3 outbound call attempts"] and [c.source_id for c in r.citations] == ["POL-COLL-001"]


def test_model_saying_not_found_is_respected(tmp_path):
    llm, _ = make(tmp_path, reply(not_found=True))
    assert answer_question("q", SRC, llm=llm, language="FR", **ASK).answer.startswith("Introuvable")


def test_owner_hint_and_conflict_note_and_confidence_clamp(tmp_path):
    llm, _ = make(tmp_path, reply(not_found=True))
    assert "Ask: Collections Policy" in answer_question("q", SRC, llm=llm, ask_owner="Collections Policy", **ASK).answer
    (tmp_path / "b").mkdir()
    llm, _ = make(tmp_path / "b", reply(conflict_note="The FAQ says 6 months.", confidence=7))  # own cache
    r = answer_question("q", SRC, llm=llm, **ASK)
    assert "FAQ says 6 months" in r.answer and r.confidence == 1.0


def test_prompt_labels_table_rows_authoritative_and_logs_the_question(tmp_path):
    class R:  # shape of a policy SearchResult
        sections = []
        hardship_rows = [{"program_id": "HP-LOAN-RED3", "version": "2026.1", "program_name": "3-month reduced payment",
                          "payment_reduction_pct": "0.5", "duration_months": "3", "policy_section": "§4.2", "notes": None}]
    src = policy_sources(R)
    assert src[0].authoritative and "duration_months: 3" in src[0].text and "policy_section" not in src[0].text
    con = duckdb.connect(":memory:")
    llm, be = make(tmp_path, reply(evidence=[{"source": "S1", "quote": "duration_months: 3"}]), audit=AuditLog(con))
    r = answer_question("How long?", src, llm=llm, user_id="A7", **ASK)
    assert "AUTHORITATIVE" in be.calls[0]["user"] and r.citations[0].section == "HP-LOAN-RED3"
    assert con.execute("SELECT count(*) FROM gold.audit_log WHERE user_id='A7'").fetchone()[0] == 1


# ---- OpenAI-compatible backend (OpenRouter) (HTTP is replaced; nothing leaves the machine) ------------------------------
class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return json.dumps(self.payload).encode()


def test_openai_compatible_backend_sends_openai_style_request_and_reads_reply(monkeypatch):
    from trustissues.common import llm as L
    seen = {}

    def fake_open(req, timeout):
        seen["url"], seen["auth"], seen["body"] = req.full_url, req.get_header("Authorization"), json.loads(req.data)
        return FakeResp({"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr(L.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(L.json, "load", lambda r: r.payload)
    monkeypatch.setenv("TEST_KEY", "k-test")
    out = L.OpenAICompatibleBackend("https://example.test/v1/", "TEST_KEY").complete(model="m", system="S", user="U", max_tokens=5, temperature=0)
    assert out == "hi" and seen["url"] == "https://example.test/v1/chat/completions" and seen["auth"] == "Bearer k-test"
    assert seen["body"]["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]


def test_missing_key_and_missing_model_give_clear_errors(monkeypatch, tmp_path):
    from trustissues.common import llm as L
    monkeypatch.delenv("TEST_KEY", raising=False)
    monkeypatch.setattr(L, "_load_dotenv", lambda: None)
    with pytest.raises(LLMError, match="TEST_KEY"):
        L.OpenAICompatibleBackend("https://example.test/v1", "TEST_KEY").complete(model="m", system="", user="", max_tokens=1, temperature=0)
    llm = LLM(Scripted(), cache=ResponseCache(tmp_path / "c.sqlite"), models={"answer": None})
    with pytest.raises(LLMError, match="no model chosen"):
        call(llm)


def test_error_object_inside_a_200_reply_is_raised(monkeypatch):
    from trustissues.common import llm as L
    monkeypatch.setattr(L.urllib.request, "urlopen", lambda req, timeout: FakeResp({"error": {"message": "rate limited"}}))
    monkeypatch.setattr(L.json, "load", lambda r: r.payload)
    monkeypatch.setenv("TEST_KEY", "k")
    with pytest.raises(LLMError, match="rate limited"):
        L.OpenAICompatibleBackend("https://example.test/v1", "TEST_KEY").complete(
            model="m", system="", user="", max_tokens=1, temperature=0)


# ---- language detection ------------------------------------------------------------------------
@pytest.mark.parametrize("q,lang", [
    ("Does the National Do Not Call List stop collection calls?", "EN"),
    ("How many call attempts can we make on a mid-stage account in a week?", "EN"),
    ("What is the grace period?", "EN"),
    ("Quelles sont les heures permises pour appeler un client?", "FR"),
    ("Combien de tentatives d'appel par semaine?", "FR"),
    ("Est-ce que la Liste nationale de numéros exclus bloque les appels de recouvrement?", "FR"),
    ("Le client peut-il demander de cesser les communications", "FR"),  # no accents, no question mark
    ("Que doit faire l'agent avant de demander un paiement?", "FR"),
    ("POL-COLL-004 §4.2", "EN"),  # nothing to go on: default
    ("", "EN"),
])
def test_language_is_detected_from_the_question(q, lang):
    from trustissues.common.language import detect_language
    assert detect_language(q) == lang


def test_answer_follows_the_question_language_without_a_flag(tmp_path):
    llm, be = make(tmp_path, reply(not_found=True))
    r = answer_question("Quelles sont les heures permises pour appeler?", SRC, llm=llm, **ASK)
    assert r.answer.startswith("Introuvable") and "French" in be.calls[0]["system"]


# ---- French question over English reference documents -------------------------------------------
def test_french_question_is_translated_for_search_and_answered_in_french(tmp_path):
    from trustissues.ask.rag.answer import answer_policy
    from trustissues.ask.rag.policy_search import PolicyIndex
    from trustissues.ask.rag.policy_ingest import Chunk
    meta = {"doc_id": "POL-1", "doc_type": "policy", "title": "Contact", "language": "EN", "version": "3.0",
            "effective_date": "2026-01-15", "superseded_by": None, "owner": "o", "confidentiality": "internal",
            "applies_to_products": ["card"], "applies_to_provinces": ["ALL"]}
    en_text = "The National Do Not Call List applies to telemarketing only and does not block collection calls."
    ix = PolicyIndex([Chunk("c1", "POL-1", "§5.3", "", f"Contact §5.3\n{en_text}", en_text, meta)])
    ok = {"answer": "Non, elle ne bloque pas les appels de recouvrement.", "not_found": False, "confidence": 0.9,
          "evidence": [{"source": "S1", "quote": "does not block collection calls"}], "conflict_note": None}
    llm, be = make(tmp_path, "Does the National Do Not Call List stop collection calls?", ok)
    r = answer_policy("La liste nationale de numéros exclus bloque-t-elle les appels de recouvrement?", ix,
                      llm=llm, as_of=date(2026, 9, 28), user_role="agent", with_hardship=False, mode="bm25")
    assert be.calls[0]["model"] == "m-bulk" and "Translate" in be.calls[0]["system"]  # step 1: cheap translation
    assert "French" in be.calls[1]["system"] and "Do Not Call" in be.calls[1]["user"]  # step 2: answer in French
    assert r.answer.startswith("Non") and r.evidence == ["does not block collection calls"]  # quote stays English
    assert r.citations[0].source_id == "POL-1"


def test_translation_failure_falls_back_to_the_original_question(tmp_path):
    from trustissues.ask.rag.answer import translate_to_english
    llm, _ = make(tmp_path, *[RuntimeError("down")] * 3)
    assert translate_to_english("Quelles heures?", llm, user_role="agent") == "Quelles heures?"


# ---- a cut-off reply is never an answer ---------------------------------------------------------
def test_truncated_reply_is_retried_with_a_bigger_limit(tmp_path):
    from trustissues.common.llm import LLMTruncated
    llm, be = make(tmp_path, LLMTruncated("cut"), LLMTruncated("cut"), "finished")
    llm.cfg = {"max_tokens_cap": 8000}
    assert call(llm, max_tokens=1000).text == "finished"
    assert [c["max_tokens"] for c in be.calls] == [1000, 2000, 4000]


def test_truncation_stops_at_the_cap(tmp_path):
    from trustissues.common.llm import LLMTruncated
    llm, be = make(tmp_path, *[LLMTruncated("cut")] * 5)
    llm.cfg = {"max_tokens_cap": 2000}
    with pytest.raises(LLMError):
        call(llm, max_tokens=1000)
    assert [c["max_tokens"] for c in be.calls] == [1000, 2000]


def test_finish_reason_length_is_raised_and_extra_body_is_sent(monkeypatch):
    from trustissues.common import llm as L
    sent = {}

    def fake_open(req, timeout):
        sent["body"] = json.loads(req.data)
        return FakeResp({"choices": [{"finish_reason": "length", "message": {"content": "half an ans"}}]})

    monkeypatch.setattr(L.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(L.json, "load", lambda r: r.payload)
    monkeypatch.setenv("TEST_KEY", "k")
    be = L.OpenAICompatibleBackend("https://example.test/v1", "TEST_KEY", extra_body={"reasoning": {"enabled": False}})
    with pytest.raises(L.LLMTruncated):
        be.complete(model="m", system="", user="", max_tokens=5, temperature=0)
    assert sent["body"]["reasoning"] == {"enabled": False}
