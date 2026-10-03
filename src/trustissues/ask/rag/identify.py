"""Find which case or customer a typed question is about, and whether it is a rulebook question.

Used by the agent dashboard so an agent types plain text ("what did the customer agree to on CS-2026-614370?") instead
of command-line flags. Deterministic, no model call. P2's router does the same job inside `ask()`; this is the
small version the dashboard needs until the two are joined.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CASE_ID = re.compile(r"\bCS-\d{4}-\d{6}\b", re.IGNORECASE)
CUSTOMER_ID = re.compile(r"\bG-\d{6,8}\b", re.IGNORECASE)
ROW_REF = re.compile(r"^\s*(?:case\s*)?#?\s*(\d{1,3})\s*$", re.IGNORECASE)  # "3", "#3", "case 3": row of my case list
PREFIX = re.compile(r"^\s*(policy|rule|rules|case|customer)\s*:\s*", re.IGNORECASE)

# Wording that asks what the rules are, not what happened with this customer. Checked only when no case or customer
# is named in the question itself.
POLICY_CUES = re.compile(r"""\b(
    policy|policies|procedure|rule|rules|regulation|regulat\w*|law|legal(ly)?|allowed|permitted|may\s+we|can\s+we|
    are\s+we|must|should\s+(the|an|i|we)\s+agent|what\s+should\s+(the|an)\s+agent|what\s+must|grace\s+period|
    programme|program|script|faq|casl|consent|do\s+not\s+call|calling\s+hours|permitted\s+hours|how\s+many\s+call|
    in\s+general|a\s+customer\s+(says|tells|filed|asks)|
    politique|règle|regle|permis|autoris\w*|doit|devons
)\b""", re.IGNORECASE | re.VERBOSE)


@dataclass
class Target:
    path: str  # customer | policy | open (only an identifier was typed: open that case)
    case_id: str | None = None
    customer_id: str | None = None
    row: int | None = None  # 1-based row of the agent's case list
    question: str = ""  # the text with any routing prefix removed


def identify(text: str, *, current_case: str | None = None) -> Target:
    """Decide what a typed line is about.

    - a case ID or customer ID in the text  -> that customer (just an ID, or a row number: open it)
    - "policy: ..." / "case: ..."           -> forced, for when the guess is wrong
    - a case is open and the line is not a rules question -> the open case
    - otherwise                              -> the policy documents
    """
    q = text.strip()
    forced = None
    m = PREFIX.match(q)
    if m:
        forced = "policy" if m.group(1).lower().startswith(("policy", "rule")) else "customer"
        q = q[m.end():].strip()

    row = ROW_REF.match(q)
    if row and forced is None:
        return Target("open", row=int(row.group(1)), question=q)
    case = CASE_ID.search(q)
    cust = CUSTOMER_ID.search(q)
    case_id = case.group(0).upper() if case else None
    cust_id = cust.group(0).upper() if cust else None
    rest = CUSTOMER_ID.sub("", CASE_ID.sub("", q)).strip(" ?.,:;-")
    if (case_id or cust_id) and not rest and forced is None:
        return Target("open", case_id=case_id, customer_id=cust_id, question=q)

    if forced == "policy":
        return Target("policy", question=q)
    if case_id or cust_id:
        return Target("customer", case_id=case_id, customer_id=cust_id, question=q)
    if forced == "customer" or (current_case and not POLICY_CUES.search(q)):
        return Target("customer", case_id=current_case, question=q)
    return Target("policy", question=q)
