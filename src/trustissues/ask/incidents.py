"""Recorded incidents that overlap a period, for "why did X change" answers. No model: a fixed lookup.

The incident reports (`docs/incidents/IR-*.md`) are in P4's policy index with `doc_type = incident_report`. Each states
when it happened ("**When**: 23-24 September 2026."). The drivers path splits a change by segment; when an incident
known by the as-of date overlaps the period being explained, the answer says so and cites it, because the split alone
cannot tell an operational cause (BQ-030: the dialer outage) from a change in customers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}
WHEN = re.compile(r"\*\*When\*\*:\s*(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s+([A-Za-z]+)\s+(\d{4})")


@dataclass(frozen=True)
class Incident:
    doc_id: str
    title: str
    start: date
    end: date  # exclusive
    known_from: date | None  # effective date of the report
    summary: str


def _summary(text: str) -> str:
    """The report's text after the "When" line's date, without markdown, headers or the synthetic-data banner."""
    body = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", ">"))]
    s = re.sub(r"\*\*When\*\*:\s*[^.]*\.\s*", "", " ".join(body), count=1)
    s = re.sub(r"\*\*Severity\*\*:\s*\S+\s*", "", s)
    return re.sub(r"\s+", " ", s.replace("**", "")).strip()


def parse(doc_id: str, title: str, text: str, effective: str | None) -> Incident | None:
    m = WHEN.search(text)
    if not m or m.group(3).lower() not in MONTHS:
        return None
    month, year = MONTHS[m.group(3).lower()], int(m.group(4))
    start = date(year, month, int(m.group(1)))
    end = date(year, month, int(m.group(2) or m.group(1))) + timedelta(days=1)
    known = date.fromisoformat(effective) if effective else None
    return Incident(doc_id, title, start, end, known, _summary(text))


@lru_cache(maxsize=1)
def _load() -> tuple[Incident, ...]:
    try:
        from trustissues.ask.rag.policy_search import PolicyIndex
        index = PolicyIndex.load()
    except (ImportError, FileNotFoundError):
        return ()
    out = []
    for c in index.chunks:
        meta = c.meta if isinstance(c.meta, dict) else {}
        if meta.get("doc_type") != "incident_report":
            continue
        inc = parse(c.doc_id, str(meta.get("title", c.doc_id)), c.section_text, meta.get("effective_date"))
        if inc:
            out.append(inc)
    return tuple(out)


def overlapping(start: date, end: date, as_of: date, incidents=None) -> list[Incident]:
    """Incidents that overlap [start, end) and whose report existed by the as-of date."""
    pool = _load() if incidents is None else incidents
    return [i for i in pool if i.start < end and start < i.end and (i.known_from is None or i.known_from <= as_of)]


def sentence(inc: Incident) -> str:
    days = f"{inc.start:%d}" + (f"-{inc.end - timedelta(days=1):%d}" if inc.end - inc.start > timedelta(days=1) else "")
    title = re.sub(r"^Incident Report:\s*", "", inc.title)
    return f"Recorded incident {inc.doc_id} ({title}, {days.lstrip('0')} {inc.start:%B %Y}): {inc.summary}"
