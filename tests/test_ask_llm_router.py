"""The model fallback only places questions the rules could not, and only into known paths and metric ids."""
from types import SimpleNamespace

import pytest

from trustissues.ask import metric_matcher
from trustissues.ask.service import classify

# Without the local metric matcher the rules send UNRULED to the SQL path, so these tests need the model files.
needs_matcher = pytest.mark.skipif(metric_matcher.match("promise kept rate") is None,
                                   reason="run `python scripts/setup_router.py` first")


class FakeLLM:
    def __init__(self, data):
        self.data, self.calls = data, []

    def complete(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(data=self.data)


UNRULED = "Of customers we reached in August 2026 what share agreed to pay?"


@needs_matcher
def test_no_model_leaves_unplaced_question_unanswerable():
    assert classify(UNRULED)[0] == "cannot_answer"


@needs_matcher
def test_model_places_a_known_metric():
    llm = FakeLLM({"path": "metric", "metric_id": "ptp_rate"})
    assert classify(UNRULED, llm) == ("metric", "ptp_rate")
    assert "customers we reached" in llm.calls[0]["user"]


@needs_matcher
def test_unknown_metric_or_path_is_ignored():
    assert classify(UNRULED, FakeLLM({"path": "metric", "metric_id": "made_up"}))[0] == "cannot_answer"
    assert classify(UNRULED, FakeLLM({"path": "rag_customer", "metric_id": None}))[0] == "cannot_answer"
    assert classify(UNRULED, FakeLLM("not a dict"))[0] == "cannot_answer"


def test_rule_matches_and_refusals_never_reach_the_model():
    llm = FakeLLM({"path": "sql"})
    assert classify("Which religious groups default the most?", llm)[0] == "refused"
    assert llm.calls == []  # a rule refusal is final: not even the model screen is asked
    assert classify("Promise kept rate in July 2026", llm)[0] == "metric"
    assert not [c for c in llm.calls if c["prompt_version"].startswith("ask_route")]  # a rule match needs no routing help


@needs_matcher
def test_wrongly_typed_fields_do_not_crash():
    for bad in ({"path": "metric", "metric_id": ["ptp_rate"]}, {"path": "metric", "metric_id": {"a": 1}},
                {"path": ["sql"], "metric_id": None}, {"path": {"x": 1}}, {"path": 3, "metric_id": 4}):
        assert classify(UNRULED, FakeLLM(bad))[0] == "cannot_answer"
