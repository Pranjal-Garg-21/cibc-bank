"""Mapping assistant, fully offline: a fake model stands in for the API, and the real _post refuses to run."""
import json

import pytest

from trustissues.factory.curate import mapping_assistant as ma

REAL_POST = ma._post  # before the autouse fixture swaps it for a guard


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("a test tried to call the real API")
    monkeypatch.setattr(ma, "_post", no_network)
    monkeypatch.setattr(ma, "CALL_LOG", tmp_path / "llm_calls.jsonl")
    monkeypatch.setattr(ma, "_cache_path", lambda: tmp_path / "cache.jsonl")
    monkeypatch.setattr(ma, "api_key", lambda: None)
    return tmp_path


def col(table, column, sensitivity="none", example="x"):
    return ma.SourceColumn(table, column, "string", "", f"{column} description", example, sensitivity)


SOURCES = [col("customers", "segment"), col("customers", "gender_code", "protected", "F"),
           col("customers", "first_name", "direct_pii", "Ryan"), col("card_accounts", "current_balance")]


def fake_model(answers):
    calls = []

    def post(prompt, key):
        calls.append(prompt)
        table = prompt.split("SOURCE TABLE: ")[1].split("\n")[0]
        return json.dumps({"mappings": [dict(column=c, **answers[(table, c)]) for t, c in answers if t == table]})
    post.calls = calls
    return post


ANSWERS = {
    ("customers", "segment"): {"target": "c360_customer.segment", "transform": "rename", "confidence": 0.95, "reason": "same field"},
    ("customers", "gender_code"): {"target": "excluded.protected", "transform": "none", "confidence": 0.99, "reason": "protected"},
    ("customers", "first_name"): {"target": "c360_customer.full_name", "transform": "concat", "confidence": 0.4, "reason": "part of name"},
    ("card_accounts", "current_balance"): {"target": "c360_account.balance", "transform": "rename", "confidence": 0.9, "reason": "balance"},
}


def test_nothing_is_sent_without_call(tmp_path):
    r = ma.propose(call=False, sources=SOURCES, path=tmp_path / "m.csv")
    assert r.batches == 2 and r.called == 0 and not (tmp_path / "m.csv").exists()


def test_no_key_refuses_before_any_call(tmp_path):
    with pytest.raises(ma.LLMError, match="no API key"):
        ma.propose(call=True, sources=SOURCES, path=tmp_path / "m.csv")


def test_sensitive_examples_are_withheld():
    prompt = ma.build_batches(SOURCES, ["customers"])[0].prompt
    assert '"Ryan"' not in prompt and '"F"' not in prompt
    assert "[withheld: direct_pii]" in prompt and "[withheld: protected]" in prompt


def test_proposals_written_lowest_confidence_first(tmp_path):
    path = tmp_path / "m.csv"
    r = ma.propose(call=True, sources=SOURCES, path=path, post=fake_model(ANSWERS))
    assert r.called == 2 and r.columns == 4
    rows = list(ma.read_csv(path).values())
    assert [float(x["confidence"]) for x in rows] == sorted(float(x["confidence"]) for x in rows)
    assert {x["status"] for x in rows} == {"proposed"}


def test_rerun_uses_cache_and_keeps_reviews(tmp_path):
    path = tmp_path / "m.csv"
    post = fake_model(ANSWERS)
    ma.propose(call=True, sources=SOURCES, path=path, post=post)
    rows = ma.read_csv(path)
    rows[("customers", "first_name")].update(status="edited", approved_target="identity.name", reviewer="anushka")
    ma.write_csv(rows, path)
    r = ma.propose(call=True, sources=SOURCES, path=path, post=post)
    assert r.batches == 0 and len(post.calls) == 2           # everything current: nothing re-asked
    assert ma.read_csv(path)[("customers", "first_name")]["approved_target"] == "identity.name"


def test_changed_dictionary_entry_is_asked_again(tmp_path):
    path = tmp_path / "m.csv"
    ma.propose(call=True, sources=SOURCES, path=path, post=fake_model(ANSWERS))
    changed = [col("customers", "segment", example="new example")] + SOURCES[1:]
    assert ma.propose(call=False, sources=changed, path=path).batches == 1


def test_invalid_answers_go_to_review_with_zero_confidence():
    b = ma.Batch("customers", [col("customers", "segment"), col("customers", "gender_code", "protected"),
                               col("customers", "tenure_months")])
    text = "```json\n" + json.dumps({"mappings": [
        {"column": "segment", "target": "made.up_target", "confidence": 0.9},
        {"column": "gender_code", "target": "decisioning_customer.segment", "confidence": 0.8}]}) + "\n```"
    out = {m["column"]: m for m in ma.parse_response(text, b, ma.targets())}
    assert out["segment"]["confidence"] == 0 and "not in catalogue" in out["segment"]["reason"]
    assert out["gender_code"]["confidence"] == 0 and "protected" in out["gender_code"]["reason"]
    assert out["tenure_months"]["confidence"] == 0 and "no answer" in out["tenure_months"]["reason"]


def test_reference_targets_all_exist_in_the_catalogue():
    cat = ma.targets()
    assert not [t for t in ma.REFERENCE.values() if t not in cat]


def test_evaluate_measures_agreement_with_hand_built_mappings(tmp_path):
    path = tmp_path / "m.csv"
    ma.propose(call=True, sources=SOURCES, path=path, post=fake_model(ANSWERS))
    e = ma.evaluate(path)
    assert e["proposed"] == 3 and e["agree"] == 3  # segment, gender_code, current_balance are in REFERENCE, all right


def test_the_real_call_goes_through_common_llm_on_the_bulk_tier(monkeypatch, tmp_path):
    """Provider, key and model come from the shared `llm:` section (merge.md §3); a stand-in backend, no network."""
    from trustissues.common import llm as shared
    seen = {}

    class Backend:
        def complete(self, **kw):
            seen.update(kw)
            return '{"mappings": []}'

    real_llm = shared.LLM
    monkeypatch.setattr(shared, "LLM", lambda: real_llm(Backend(), cache=shared.ResponseCache(tmp_path / "c.sqlite"),
                                                        models={"bulk": "bulk-model"}))
    assert REAL_POST("map these columns") == '{"mappings": []}'
    assert seen["model"] == "bulk-model" and seen["user"] == "map these columns" and seen["system"] == ma.SYSTEM
    assert ma.model() == shared.config.settings()["llm"]["models"]["bulk"]
