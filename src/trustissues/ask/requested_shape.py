"""Recognise requested quantities before retrieving a nearby metric topic.

These guards describe query shapes, not metric formulas or access decisions.
Unqualified population counts and distributions belong to generated SQL; a
rate, cost or average must not be inferred just from the entities mentioned.
"""
from __future__ import annotations

import re

STATISTIC = r"\b(rate|share|percentage|percent|fraction|proportion|average|mean|median)\b"
UNIT_RATIO = r"\b(?:per|each)\s+(?:open\s+)?(?:case|account|contact|call|attempt|rpc|customer)s?\b"
COST = r"\b(costs?|expenses?|expenditure|spend|spending)\b"
COST_UNIT = (
    r"\b(?:per|each)\s+(?:(?:outbound|inbound|successful|recorded)\s+)?"
    r"(?P<unit>right[- ]party(?:\s+contact)?|rpc|contacts?|calls?|attempts?|dials?)\b"
)
ENTITY = r"\b(cases?|accounts?)\b"
POPULATION = (
    r"\b(?:how many|number of|count(?: of)?|total)\s+"
    r"(?:(?:open|active|closed|outbound|inbound|collections|customer|current|all)\s+){0,3}"
    r"(?:cases?|accounts?|contacts?|calls?|attempts?|dials?)\b"
)
# These identify a qualified metric population, which must still be interpreted
# by the catalogue. A bare contact count has none of these restrictions.
QUALIFIED = (
    r"\b(disput\w*|hardship|cure\w*|payroll|promis\w*|kept|broken|"
    r"reached?|borrower|holder|customer|right[- ]party|rpc|outside|"
    r"permitted|allowed|hours?|return\w*|charged|written|write[- ]?off|that|where|which)\b"
)


def requested_shape(question: str) -> str | None:
    """Return only shapes that can resolve topic/quantity conflicts explicitly."""
    unit = re.search(COST_UNIT, question, re.I)
    if re.search(COST, question, re.I) and unit:
        if re.fullmatch(r"right[- ]party(?:\s+contact)?|rpc", unit["unit"], re.I):
            return "cost_per_rpc"
        return "cost_per_contact"
    if (re.search(r"\b(distribution|breakdown|break down)\b", question, re.I)
            and re.search(ENTITY, question, re.I)
            and not re.search(STATISTIC + "|" + UNIT_RATIO, question, re.I)):
        return "population"
    if (re.search(POPULATION, question, re.I)
            and not re.search(STATISTIC + "|" + UNIT_RATIO, question, re.I)
            and not re.search(COST, question, re.I)
            and not re.search(QUALIFIED, question, re.I)):
        return "population"
    return None
