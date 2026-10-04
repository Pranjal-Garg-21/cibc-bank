"""Documented Ask access matrix on published data, with explicit real-model opt-in.

Expectations are fixed from docs/interfaces.md (Case to call §1–2) and
docs/p2_design_sections.md (pipeline steps 5 and 7), not discovered by asking.
Leads use the assigned agent's lead ID; unassigned open work is also visible to
leads. Analysts cannot read individual calls. Agents need an assignment or NBA
queue grant. The later P4 team re-check takes precedence over the earlier broad
lead-access statement: registered in-team leads pass; out-of-team leads without
a separate queue or unassigned-work grant receive the same missing-case denial.
"""
from dataclasses import dataclass
import re

import pytest

from trustissues.ask.access import case_customer_resolver
from trustissues.ask.service import ask
from trustissues.ask.types import UserContext


ROLES = ("agent", "team_lead", "analyst")


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    path: str
    certified: bool = False
    reason: str | None = None


# Twelve questions, each checked for all three roles. Expected permissions are
# spelled out here, deliberately independent of the application's PATH_ROLES.
QUESTIONS = (
    Question("kept", "Promise-kept rate during June 2026 by product", "metric", True),
    Question("rpc", "RPC rate during July 2026 by province", "metric", True),
    Question("writeoff", "Write-off rate during June 2026 by product", "metric", True),
    Question("drivers", "Why did RPC rate change between April 2026 and May 2026?", "drivers", True),
    Question("sql", "How many open cases are in each queue?", "sql"),
    Question("policy_consent", "Under CASL policy, is a reminder email permitted when express consent was withdrawn?", "rag_corpus"),
    Question("policy_proposal", "Under collections policy, what should the agent do after a borrower reports a consumer proposal?", "rag_corpus"),
    Question("assigned_call", "Summarise the most recent recorded call for case {assigned}.", "rag_customer"),
    Question("unassigned_call", "Summarise the most recent recorded call for case {unassigned}.", "rag_customer"),
    Question("protected", "Compare delinquency rates by customer gender.", "refused", reason="protected_attributes"),
    Question("export", "Export all customer email addresses and phone numbers.", "refused", reason="pii_export"),
    Question("lookalike", "Under collections policy, should an agent read a recording notice on phone calls?", "rag_corpus"),
)


def expected(question, role):
    if question.reason:
        return "refused", question.reason
    if question.path in {"metric", "drivers", "sql"} and role == "agent":
        return "refused", "access"
    if question.path == "rag_customer" and (role == "analyst" or role == "agent" and question.id == "unassigned_call"):
        return "refused", "access"
    return question.path, None


def needs_model(question, role):
    path, _ = expected(question, role)
    return path in {"sql", "rag_corpus", "rag_customer"}


MATRIX = [pytest.param(q, role, id=f"{q.id}-{role}",
                       marks=[pytest.mark.llm] if needs_model(q, role) else [])
          for q in QUESTIONS for role in ROLES]

PII = {
    "email": r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    "phone": r"(?<!\w)(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\w)",
    "date_of_birth": r"\b(?:dob|born|date of birth|birth date)\b.{0,35}\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b",
    "card_number": r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)",
}


def card_detail(card):
    return (f"path={card.path}, refused={card.refused}, reason={card.refusal_reason}, "
            f"certified={card.certified}, rows={len(card.rows)}, sources={len(card.sources)}, "
            f"answer={card.answer[:300]!r}")


def assert_card_hygiene(card, certified=False, require_evidence=True):
    hits = [(kind, m.group()) for kind, pattern in PII.items() for m in re.finditer(pattern, card.answer, re.I)]
    assert not hits, f"PII_hit_count={len(hits)}, hits={hits}; {card_detail(card)}"
    assert isinstance(card.certified, bool) and card.certified is certified, card_detail(card)
    if card.refused:
        assert not card.rows and not card.columns and not card.sql, card_detail(card)
    elif require_evidence:
        assert card.sql or card.sources, "Answered card lacks provenance: " + card_detail(card)
        if certified or card.path == "sql":
            assert card.sql and card.rows, "Answered analytical card lacks SQL/data: " + card_detail(card)


def user_for(role, samples):
    return UserContext(samples["agent"] if role == "agent" else samples["lead"] if role == "team_lead" else "integration-analyst", role)


@pytest.mark.parametrize("question,role", MATRIX)
def test_documented_question_role_matrix(question, role, warehouse, as_of, case_samples, request):
    path, reason = expected(question, role)
    kwargs = dict(llm=None, con=warehouse, resolver=case_customer_resolver(warehouse, as_of))
    if needs_model(question, role):
        kwargs.update(llm=request.getfixturevalue("real_llm"), rag=request.getfixturevalue("real_rag"))
    card = ask(question.text.format(**case_samples), user_for(role, case_samples), as_of_date=as_of, **kwargs)
    assert (card.path, card.refusal_reason) == (path, reason), (
        f"expected_path={path}, expected_reason={reason}, role={role}; {card_detail(card)}")
    assert card.refused is (reason is not None), card_detail(card)
    assert_card_hygiene(card, certified=question.certified and reason is None)


@pytest.mark.parametrize("question", [q for q in QUESTIONS if q.path == "rag_corpus"], ids=lambda q: q.id)
@pytest.mark.parametrize("role", ROLES)
def test_policy_and_contact_lookalike_access_without_model(question, role, warehouse, as_of):
    # No fake model/RAG: this checks the public path and its no-model response;
    # the marked matrix checks actual policy answers and evidence.
    card = ask(question.text, UserContext("integration-policy-" + role, role),
               as_of_date=as_of, con=warehouse, llm=None)
    assert card.path == "rag_corpus" and not card.refused, card_detail(card)
    assert_card_hygiene(card, require_evidence=False)


@pytest.mark.parametrize("role", ["agent", "analyst"])
def test_missing_case_and_inaccessible_case_have_identical_denials(role, warehouse, as_of, case_samples):
    user = UserContext(case_samples["other_agent"] if role == "agent" else "integration-analyst", role)
    resolver = case_customer_resolver(warehouse, as_of)
    cards = [ask(f"Summarise the most recent call for case {case}.", user,
                 as_of_date=as_of, con=warehouse, resolver=resolver, llm=None)
             for case in (case_samples["assigned"], case_samples["missing"])]
    for card in cards:
        assert card.refused and card.refusal_reason == "access", card_detail(card)
        assert_card_hygiene(card)
    assert cards[0].answer == cards[1].answer, f"Existing-case denial={cards[0].answer!r}, missing-case denial={cards[1].answer!r}"


@pytest.mark.parametrize("membership", [
    pytest.param("in_team", marks=pytest.mark.llm), "out_of_team",
])
def test_registered_team_lead_case_access(membership, warehouse, as_of, case_samples, lead_team_samples, request):
    user = UserContext(lead_team_samples[membership], "team_lead")
    kwargs = dict(con=warehouse, resolver=case_customer_resolver(warehouse, as_of), llm=None)
    if membership == "in_team":
        kwargs.update(llm=request.getfixturevalue("real_llm"), rag=request.getfixturevalue("real_rag"))
    else:
        # Exercise the actual P4 team re-check, with no provider or cache. Any
        # attempt to ask a model before denying this lead makes this test fail.
        from trustissues.ask.rag_handler import make_rag_handler
        class NoModelCalls:
            def complete(self, **kwargs):
                raise AssertionError("Out-of-team denial must happen before a model call")
        kwargs["rag"] = make_rag_handler(None, NoModelCalls(), con=warehouse)
    question = QUESTIONS[7].text.format(assigned=lead_team_samples["case_id"])
    card = ask(question, user, as_of_date=as_of, **kwargs)
    if membership == "in_team":
        assert card.path == "rag_customer" and not card.refused, card_detail(card)
    else:
        assert card.refused and card.refusal_reason == "access", card_detail(card)
        missing = ask(QUESTIONS[7].text.format(assigned=case_samples["missing"]), user,
                      as_of_date=as_of, **kwargs)
        assert missing.refused and missing.refusal_reason == "access", card_detail(missing)
        assert_card_hygiene(missing)
        assert card.answer == missing.answer, f"Existing-case denial={card.answer!r}, missing-case denial={missing.answer!r}"
    assert_card_hygiene(card)
