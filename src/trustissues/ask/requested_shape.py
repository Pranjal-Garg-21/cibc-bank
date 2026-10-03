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


def requests_entity_count(question: str) -> bool:
    """An explicit count of cases/accounts cannot be answered by an inferred mean.

    Descriptors may name a queue or state. Relative clauses describe filters;
    they do not turn the requested quantity into a rate or an average.
    """
    count = re.search(
        r"\b(?:how many|number of|count(?: of)?|total(?: number of)?)\s+"
        r"(?:[a-z][\w-]*\s+){0,8}(?:cases?|accounts?)\b", question, re.I)
    if not count:
        return False
    if re.search(r"\b(?:days?|hours?|minutes?|weeks?|months?|years?)\b", count[0], re.I):
        return False  # counts of time units describe an age/duration, not entities
    # A mean number of units and contacts per case request a statistic. A count
    # of cases *with* an average-DPD field still requests a population count.
    return not (re.search(STATISTIC, question[:count.start()], re.I)
                or re.search(r"\bon (?:the )?average\b|" + UNIT_RATIO, question, re.I)
                or re.search(COST, question, re.I))


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
