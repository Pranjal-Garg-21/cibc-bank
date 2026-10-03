"""Which case or customer a typed line is about, and whether it goes to the policy documents."""
import pytest

from trustissues.ask.rag.identify import identify

CASE = "CS-2026-614370"


@pytest.mark.parametrize("text, current, path, case", [
    # benchmark wording: the case ID is in the question
    (f"Summarise the most recent recorded call on case {CASE}: what did the customer say and agree to?", None, "customer", CASE),
    (f"what did they promise on {CASE.lower()}", None, "customer", CASE),
    # with a case open, plain questions are about it
    ("what did the customer agree to on the last call?", CASE, "customer", CASE),
    ("did they pay anything this month?", CASE, "customer", CASE),
    ("Qu'est-ce que le client a promis?", CASE, "customer", CASE),
    # rules questions go to the policy documents, open case or not
    ("What is the grace period before a promise to pay is considered broken?", CASE, "policy", None),
    ("A customer says their hours were cut at work. What must the agent do before asking for payment?", CASE, "policy", None),
    ("Does the National Do Not Call List stop collection calls?", None, "policy", None),
    ("Is CASL express consent needed to send a payment reminder by email?", CASE, "policy", None),
    # no case open and no rules wording: still the documents (nothing else to read)
    ("what about weekends?", None, "policy", None),
    # forcing
    ("policy: what did they agree to?", CASE, "policy", None),
    ("case: must they pay the full amount?", CASE, "customer", CASE),
])
def test_routing(text, current, path, case):
    t = identify(text, current_case=current)
    assert (t.path, t.case_id) == (path, case)


@pytest.mark.parametrize("text, row, case, cust", [
    ("3", 3, None, None), ("#12", 12, None, None), ("case 2", 2, None, None),
    (CASE, None, CASE, None), (f" {CASE.lower()} ", None, CASE, None), ("G-0200543", None, None, "G-0200543"),
])
def test_an_identifier_alone_opens_it(text, row, case, cust):
    t = identify(text)
    assert (t.path, t.row, t.case_id, t.customer_id) == ("open", row, case, cust)


def test_the_prefix_is_removed_from_the_question():
    assert identify("policy: grace period?").question == "grace period?"
