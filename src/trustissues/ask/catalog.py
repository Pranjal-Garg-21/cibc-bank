"""Certified-metric catalogue: how a question maps to a metric template call.

Rules first. A metric is chosen by trigger phrases, parameters come from the question's dates and wording,
and anything the template cannot express returns `Unsupported` (an explicit "cannot answer"), never a nearby formula.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .asof import Period, days_before, parse_periods
from .data.metric_templates import DIMENSIONS, FAMILIES
from .requested_shape import requests_entity_count

# (metric_id, trigger regex). First match wins, so more specific phrases come first.
TRIGGERS: list[tuple[str, str]] = [
    ("dispute_count", r"disputes?\b.{0,40}\b(?:raised|filed|logged)|\b(?:raised|filed)\b.{0,30}\bdisputes?"),
    ("out_of_hours_share", r"outside (?:of )?permitted (?:calling )?hours|out[- ]of[- ]hours"),
    ("broken_promise_rate", r"broken[- ]promise|promises? (?:were |was )?broken|broke a promise"),
    ("promise_kept_rate", r"promise[- ]kept|kept rate|promises? kept"),
    ("cost_per_rpc", r"cost per (?:right[- ]party|rpc)"),
    ("cost_per_contact", r"cost per contact"),
    ("rpc_rate", r"right[- ]party contact|\brpc\b"),
    ("ptp_rate", r"promise[- ]to[- ]pay rate|\bptp rate\b"),
    ("roll_rate", r"roll(?:ed)? (?:rate|to|forward)|roll rate|flow rate"),
    ("cure_rate", r"\bcure[ds]?\b|\bcure rate\b|back to current"),
    ("hardship_rate", r"hardship rate"),
    ("write_off_rate", r"charged[- ]off|write[- ]?offs?"),
    ("collected_amount", r"(?:how much|amount).{0,20}paid against promises|collected amount"),
    ("average_dpd", r"(?:average|mean) (?:days past due|dpd)"),
    ("contact_attempts_per_case", r"contact attempts?.{0,40}(?:per case|open cases|on average)|attempts per case"),
    ("delinquency_rate", r"(?:share|rate|percentage).{0,60}(?:\d+ or more )?days? past due|delinquency (?:rate|share|percentage)"),
    ("payroll_delay_share", r"payroll.{0,30}delay"),
]

# Wording -> approved dimension (checked against the metric family's allow-list).
DIM_WORDS: list[tuple[str, str]] = [
    (r"\bby (?:local )?hour(?: of day)?\b", "contact_hour_local"),
    (r"\bby agent site\b|\bby site\b", "agent_site"),
    (r"\bby (?:card )?product code\b|\bby card product\b", "product_code"),
    (r"\bby primary product\b", "primary_product"),
    (r"\bby (?:customer )?province\b", "province"),
    (r"\bby queue\b", "queue"),
    (r"\bby channel\b", "channel"),
    (r"\bby current bucket\b|\bby bucket\b", "reported_bucket"),
    (r"champion.{0,40}challenger|test cell|\bby strategy\b", "test_cell"),
    (r"number of previously broken promises|previous(?:ly)? broken", "prior_broken_ptp_count"),
    (r"\bby product\b", "product"),
]


@dataclass
class MetricCall:
    metric_id: str
    parameters: dict
    dimensions: tuple[str, ...] = ()
    variant: str = "default"
    cap_at: int | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Unsupported:
    reason: str


def detect_metric(question: str) -> str | None:
    q = question.lower()
    for metric_id, rx in TRIGGERS:
        if re.search(rx, q):
            return metric_id
    return None


def match_metric(question: str):
    """Local metadata retrieval for questions not covered by deterministic triggers."""
    from .metric_matcher import match
    return match(question)


def _dimensions(question: str, metric_id: str) -> tuple[tuple[str, ...], str | None]:
    q = question.lower()
    # Coordinate axes inherit 'by': 'by product and queue' requests both.
    # Expand that syntax before checking the same dimension allow-list.
    for _ in range(3):
        insertions = set()
        for rx, _dim in DIM_WORDS:
            for match in re.finditer(rx, q):
                if not match[0].startswith("by "):
                    continue
                following = re.match(r"\s*(?:,\s*(?:and\s+)?|and\s+)(?P<axis>[a-z][\w-]*)\b", q[match.end():])
                if following and following['axis'] not in {
                        "by", "for", "in", "from", "to", "with", "without", "compared", "show", "report"}:
                    insertions.add(match.end() + following.start('axis'))
        if not insertions:
            break
        for position in sorted(insertions, reverse=True):
            q = q[:position] + "by " + q[position:]
    allowed = DIMENSIONS[FAMILIES[metric_id]]
    dims: list[str] = []
    recognised = []
    for rx, dim in DIM_WORDS:
        matches = list(re.finditer(rx, q))
        recognised.extend(m.span() for m in matches)
        if matches and dim not in dims:
            if dim == "product" and "product_code" in dims:
                continue
            if dim not in allowed:
                return (), f"Grouping by '{dim}' is not approved for {metric_id.replace('_', ' ')}."
            dims.append(dim)
    # Recognising only approved words must not silently discard another grain.
    # Individual-entity rankings imply a breakdown even without the word 'by'.
    entity = r"(?:customers?|borrowers?|agents?|accounts?|cases?)"
    if re.search(rf"\b(?:individual|named|specific)\s+{entity}\b|"
                 rf"\b(?:which|rank|list)\s+(?:individual\s+)?{entity}\b", q):
        return (), "Individual-entity results are not an approved breakdown for this certified metric."
    for grouping in re.finditer(r"\b(?:by|per|for each|for every|on each)\s+(?:[a-z][\w-]*|\d+)\b", q):
        if any(start <= grouping.start() and grouping.end() <= end for start, end in recognised):
            continue
        tail = q[grouping.start():]
        # 'By the end of June' is a date boundary, not a grouping request.
        if re.match(r"by\s+(?:(?:the\s+)?(?:end|start|beginning)\b|\d|"
                    r"january\b|february\b|march\b|april\b|may\b|june\b|july\b|"
                    r"august\b|september\b|october\b|november\b|december\b|today\b|tomorrow\b)", tail):
            continue
        # These units are denominators already represented by the named metric.
        units = {"contact_attempts_per_case": r"per\s+(?:open\s+)?cases?\b",
                 "cost_per_contact": r"per\s+(?:contacts?|calls?|attempts?|dials?)\b",
                 "cost_per_rpc": r"per\s+(?:rpc|right[- ]party(?:\s+contact)?)\b"}
        if metric_id in units and re.match(units[metric_id], tail):
            continue
        return (), f"The requested breakdown '{grouping[0]}' is not approved for {metric_id.replace('_', ' ')}."
    return tuple(dims), None


def _clamp(p: Period, as_of: date) -> tuple[date, date]:
    """Data cannot exist after the as-of date: clamp the exclusive end to as_of + 1 day."""
    return p.start, min(p.end, as_of + timedelta(days=1))


def build_call(question: str, metric_id: str, as_of: date) -> MetricCall | Unsupported:
    q = question.lower()
    if metric_id == "average_dpd" and requests_entity_count(question):
        return Unsupported("A count of cases or accounts cannot be answered with average days past due.")
    periods = parse_periods(question, as_of)
    dims, bad = _dimensions(question, metric_id)
    if bad:
        return Unsupported(bad)
    observation_end = (as_of + timedelta(days=1)).isoformat()
    call = MetricCall(metric_id, {}, dims)
    random_only = bool(re.search(r"randomi[sz]ed|champion|challenger", q))

    # Shapes no reviewed template expresses: say so rather than answer with a nearby formula.
    if metric_id == "cure_rate" and re.search(r"certified|certification|pilot|vulnerable", q):
        return Unsupported("A certification-pilot comparison needs a reviewed variant (assignment-time certification, "
                           "randomised hardship cases); the generic cure rate would not answer it.")
    if re.search(r"\bhistor(?:y|ical)\b|\btrend over time\b", q) and metric_id not in ("roll_rate",):
        return Unsupported("Historical state cannot be rebuilt from current-only data for this metric.")

    if metric_id == "dispute_count":
        if not periods:
            return Unsupported("Which period? e.g. 'between 15 and 22 September 2026'.")
        before = re.search(r"(\d+)\s+days?\s+before", q)
        if not before:
            return Unsupported("Disputes are counted for two periods; name the comparison, e.g. 'compared with the 8 days before'.")
        s, e = _clamp(periods[0], as_of)
        prev = days_before(Period(s, e, periods[0].label), int(before[1]))
        call.parameters = {"start": s, "end": e, "comparison_start": prev.start, "comparison_end": prev.end}
        return call
    if metric_id == "out_of_hours_share":
        if not periods:
            return Unsupported("Which period? e.g. 'in September 2026'.")
        if dims != ("province",):
            return Unsupported("Out-of-hours share is reported by customer province.")
        call.parameters = {"start": _clamp(periods[0], as_of)[0]}
        call.notes.append("Outbound calls from the start date; permitted-hours flag as supplied by the source.")
        return call

    if metric_id in ("broken_promise_rate", "promise_kept_rate"):
        if not periods:
            periods = [Period(date(2000, 1, 1), as_of + timedelta(days=1), "all history")]
            call.notes.append("No period given: all promises due up to the as-of date with a known outcome.")
        s, e = _clamp(periods[0], as_of)
        call.parameters = {"start": s, "end": e, "observation_end": observation_end, "random_only": random_only}
        if "prior_broken_ptp_count" in dims:
            call.cap_at = 3
            call.notes.append("Prior broken promises of 3 or more are grouped as '3'.")
    elif metric_id == "collected_amount":
        if not periods:
            return Unsupported("Which period should the payments cover?")
        s, e = _clamp(periods[0], as_of)
        call.parameters = {"start": s, "end": e, "observation_end": observation_end}
    elif metric_id in ("rpc_rate", "ptp_rate", "cost_per_contact", "cost_per_rpc", "contact_attempts_per_case"):
        if not periods and metric_id == "contact_attempts_per_case":
            periods = [Period(date(2000, 1, 1), as_of + timedelta(days=1), "all time")]
            call.notes.append("No period given: all contacts up to the as-of date.")
        if not periods:
            return Unsupported("Which period? Contact metrics need a date range, e.g. 'in September 2026'.")
        s, e = _clamp(periods[0], as_of)
        call.parameters = {"start": s, "end": e}
    elif metric_id == "cure_rate":
        if not periods:
            return Unsupported("Which period or opening cohort?")
        s, e = _clamp(periods[0], as_of)
        if re.search(r"opened in|within 30 days", q):
            call.variant = "cohort_30d"
            call.notes.append("Cohort of cases opened in the period, cured within 30 days of opening.")
        call.parameters = {"start": s, "end": e, "observation_end": observation_end}
    elif metric_id == "hardship_rate":
        if not periods:
            return Unsupported("Which opening period? e.g. 'cases opened in 2026'.")
        s, e = _clamp(periods[0], as_of)
        call.parameters = {"start": s, "end": e}
    elif metric_id == "delinquency_rate":
        product = "card" if re.search(r"\bcard\b", q) else None
        call.parameters = {"snapshot_date": as_of, "product": product}
    elif metric_id == "write_off_rate":
        if not periods:
            return Unsupported("Which period should the charge-offs cover?")
        s, e = _clamp(periods[0], as_of)
        call.parameters = {"start": s, "end": e, "snapshot_date": as_of}
    elif metric_id == "roll_rate":
        if len(periods) < 2:
            return Unsupported("Roll rate needs a start and an end month-end, e.g. 'end of March 2026 ... end of June 2026'.")
        rng = re.search(r"(\d+)\s*-\s*(\d+)\s*days? past due", q)
        lo, hi = (int(rng[1]), int(rng[2])) if rng else (1, 30)
        call.parameters = {"start_snapshot": periods[0].start, "end_snapshot": periods[1].start,
                           "start_dpd_min": lo, "start_dpd_max": hi, "exclude_holds": False}
    elif metric_id == "average_dpd":
        call.parameters = {}
        call.notes.append("Open cases only, as of the as-of date.")
    elif metric_id == "payroll_delay_share":
        call.parameters = {"snapshot_date": as_of}
    else:
        return Unsupported("No parameter rules for this metric.")
    return call
