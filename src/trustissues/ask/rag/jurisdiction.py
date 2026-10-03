"""Which province a question is about, so province-specific rules (the Quebec annex) are always applied.

Deterministic: matches province names and main cities as whole words. If the question names no province, or names
more than one, nothing is returned and no province filter is applied. When the customer's province is known (from
their C360 record) the caller passes it directly and this is not used.
"""
from __future__ import annotations

import re

_PLACES = {
    "QC": ["quebec", "québec", "quebec city", "québec city", "montreal", "montréal", "laval", "gatineau", "sherbrooke"],
    "ON": ["ontario", "toronto", "ottawa", "mississauga", "hamilton", "london ontario"],
    "BC": ["british columbia", "vancouver", "victoria", "surrey"],
    "AB": ["alberta", "calgary", "edmonton"],
    "MB": ["manitoba", "winnipeg"],
    "SK": ["saskatchewan", "regina", "saskatoon"],
    "NS": ["nova scotia", "halifax"],
    "NB": ["new brunswick", "moncton", "fredericton"],
    "NL": ["newfoundland", "labrador", "st. john's"],
    "PE": ["prince edward island", "charlottetown"],
}
_PATTERNS = {code: re.compile(r"(?<!\w)(?:" + "|".join(re.escape(p) for p in places) + r")(?!\w)", re.IGNORECASE)
             for code, places in _PLACES.items()}
_QC_CODE = re.compile(r"\bQC\b")  # the code itself is only trusted in capitals; "on" and "ns" are ordinary words


def detect_province(question: str) -> str | None:
    found = {code for code, pat in _PATTERNS.items() if pat.search(question)}
    if _QC_CODE.search(question):
        found.add("QC")
    return found.pop() if len(found) == 1 else None
