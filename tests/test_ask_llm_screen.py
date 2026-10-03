"""The model screen can only add a refusal; it never lifts one and a bad reply never blocks a question."""
from datetime import date
from types import SimpleNamespace

from trustissues.ask.llm_screen import REASONS, check
from trustissues.ask.service import ask, classify
from trustissues.ask.types import UserContext

LEAD = UserContext("lead", "team_lead")


class FakeLLM:
    def __init__(self, data):
        self.data, self.calls = data, []

    def complete(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(data=self.data)


def test_refuse_verdict_returns_category_and_reason():
    cat, reason = check("anything", FakeLLM({"verdict": "refuse", "category": "pii_export"}))
    assert cat == "pii_export" and reason == REASONS["pii_export"]


def test_allow_unknown_category_and_malformed_replies_change_nothing():
    for data in ({"verdict": "allow", "category": "none"}, {"verdict": "refuse", "category": "made_up"},
                 {"verdict": "refuse", "category": ["pii_export"]}, {"verdict": ["refuse"], "category": "pii_export"},
                 "refuse", None, {}):
        assert check("anything", FakeLLM(data)) is None


def test_a_failing_model_does_not_block_a_question():
    from trustissues.common.llm import LLMError

    class Broken:
        def complete(self, **kw):
            raise LLMError("down")

    assert check("How many open cases are there?", Broken()) is None


def test_ask_refuses_without_touching_data_and_records_the_category():
    llm = FakeLLM({"verdict": "refuse", "category": "protected_attributes"})
    card = ask("Some reworded request the rules do not know", LEAD, as_of_date=date(2026, 9, 28), llm=llm)
    assert card.refused and card.refusal_reason == "model_screen:protected_attributes" and card.sql is None
    assert classify("Some reworded request the rules do not know", llm)[0] == "refused"


def test_rule_refusals_never_reach_the_model_and_no_model_means_no_screen():
    llm = FakeLLM({"verdict": "allow", "category": "none"})
    assert ask("Which religious groups default the most?", LEAD, as_of_date=date(2026, 9, 28), llm=llm).refused
    assert llm.calls == []
    assert classify("Some reworded request the rules do not know")[0] != "refused"  # no llm: rules only
