"""Resolve date phrases in a question to half-open [start, end) intervals. Never reads the clock."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}
_M = "|".join(MONTHS)
_MON = rf"({_M})"


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # exclusive
    label: str


def _next_month(y: int, m: int) -> date:
    return date(y + (m == 12), m % 12 + 1, 1)


def _shift_months(d: date, n: int) -> date:
    idx = d.year * 12 + d.month - 1 + n
    return date(idx // 12, idx % 12 + 1, min(d.day, calendar.monthrange(idx // 12, idx % 12 + 1)[1]))


def parse_periods(question: str, as_of: date) -> list[Period]:
    """Periods mentioned in the question, in order of appearance. Empty when none is found.

    "today" / "open now" style wording is the as-of date itself: [as_of, as_of + 1 day).
    """
    q = question.lower()
    found: list[tuple[int, Period]] = []

    def add(m: re.Match, p: Period):
        found.append((m.start(), p))

    taken: list[tuple[int, int]] = []

    def free(m: re.Match) -> bool:
        return not any(m.start() < b and a < m.end() for a, b in taken)

    def claim(m: re.Match):
        taken.append((m.start(), m.end()))

    # "12 months to 28 September 2026"
    for m in re.finditer(rf"(\d+)\s+months?\s+(?:to|ending|up to)\s+(\d{{1,2}})\s+{_MON}\s+(\d{{4}})", q):
        end = date(int(m[4]), MONTHS[m[3]], int(m[2])) + timedelta(days=1)
        add(m, Period(_shift_months(end, -int(m[1])), end, m[0]))
        claim(m)
    # "between 15 and 22 September 2026" / "15-22 September 2026"
    for m in re.finditer(rf"(?:between\s+)?(\d{{1,2}})\s*(?:and|-|to|–)\s*(\d{{1,2}})\s+{_MON}\s+(\d{{4}})", q):
        if free(m):
            y, mo = int(m[4]), MONTHS[m[3]]
            add(m, Period(date(y, mo, int(m[1])), date(y, mo, int(m[2])) + timedelta(days=1), m[0]))
            claim(m)
    # "July-September 2026"
    for m in re.finditer(rf"{_MON}\s*(?:-|–|to|through)\s*{_MON}\s+(\d{{4}})", q):
        if free(m):
            y = int(m[3])
            add(m, Period(date(y, MONTHS[m[1]], 1), _next_month(y, MONTHS[m[2]]), m[0]))
            claim(m)
    # "end of March 2026" -> that month's last day
    for m in re.finditer(rf"end of {_MON}\s+(\d{{4}})", q):
        if free(m):
            y, mo = int(m[2]), MONTHS[m[1]]
            nxt = _next_month(y, mo)
            add(m, Period(nxt - timedelta(days=1), nxt, m[0]))
            claim(m)
    # "September 2026" / "in September 2026"
    for m in re.finditer(rf"{_MON}\s+(\d{{4}})", q):
        if free(m):
            y, mo = int(m[2]), MONTHS[m[1]]
            add(m, Period(date(y, mo, 1), _next_month(y, mo), m[0]))
            claim(m)
    # "23-24 September" without a year uses the as-of year
    for m in re.finditer(rf"(\d{{1,2}})\s*(?:and|-|to|–)\s*(\d{{1,2}})\s+{_MON}(?!\s+\d{{4}})", q):
        if free(m):
            mo = MONTHS[m[3]]
            add(m, Period(date(as_of.year, mo, int(m[1])), date(as_of.year, mo, int(m[2])) + timedelta(days=1), m[0]))
            claim(m)
    # bare year: "cases opened in 2026"
    for m in re.finditer(r"\b(?:in|during|for|of)\s+(20\d\d)\b", q):
        if free(m):
            y = int(m[1])
            add(m, Period(date(y, 1, 1), date(y + 1, 1, 1), m[0]))
            claim(m)
    for m in re.finditer(r"\b(today|right now|currently|as of now)\b", q):
        if free(m):
            add(m, Period(as_of, as_of + timedelta(days=1), m[0]))
    # "the 8 days before" is relative to the previous period; handled by the router, not here.
    return [p for _, p in sorted(found, key=lambda t: t[0])]


def days_before(period: Period, n: int) -> Period:
    """The n days immediately before a period, half-open."""
    return Period(period.start - timedelta(days=n), period.start, f"{n} days before {period.label}")
