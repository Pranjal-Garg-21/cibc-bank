"""Certified-metric catalogue: how a question maps to a metric template call.

Rules first. A metric is chosen by trigger phrases, parameters come from the question's dates and wording,
and anything the template cannot express returns `Unsupported` (an explicit "cannot answer"), never a nearby formula.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .asof import MONTHS, Period, days_before, parse_periods
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


def requests_contact_quantity(question: str) -> bool:
    """Bind a contact-count numerator to a case denominator before topic retrieval.

    Cost and duration quantities are different measures, even when their context
    mentions attempts. This describes the requested unit, not a metric synonym.
    """
    q = question.lower()
    if re.search(r"\b(costs?|expenses?|expenditure|spend|spending|cad|dollars?)\b", q):
        return False
    contacts = r"\b(?:contacts?|attempts?|touch(?:es)?|calls?|dials?)\b"
    duration = re.search(r"\b(?:average|mean)\s+(?:number of\s+)?(?:days?|hours?|minutes?|dpd)\b", q)
    contact = re.search(contacts, q)
    if duration and (not contact or duration.start() < contact.start()):
        return False
    cases = r"\b(?:per|each|every)\s+(?:(?:open|active|collections)\s+)?cases?\b"
    return bool(re.search(contacts + r"[^.;?]{0,80}" + cases, q)
                or re.search(cases + r"[^.;?]{0,80}" + contacts, q)
                or re.search(r"\b(?:average|mean)\s+(?:[a-z]+\s+){0,4}" + contacts
                             + r"[^.;?]{0,80}\b(?:open|active)\s+cases?\b", q))


def detect_metric(question: str) -> str | None:
    q = question.lower()
    if requests_contact_quantity(q):
        return "contact_attempts_per_case"
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
        units = {"contact_attempts_per_case": r"(?:per|for each|for every|on each)\s+(?:(?:open|active|collections)\s+)?cases?\b",
                 "cost_per_contact": r"per\s+(?:contacts?|calls?|attempts?|dials?)\b",
                 "cost_per_rpc": r"per\s+(?:rpc|right[- ]party(?:\s+contact)?)\b"}
        if metric_id in units and re.match(units[metric_id], tail):
            continue
        return (), f"The requested breakdown '{grouping[0]}' is not approved for {metric_id.replace('_', ' ')}."
    return tuple(dims), None


def _clamp(p: Period, as_of: date) -> tuple[date, date]:
    """Data cannot exist after the as-of date: clamp the exclusive end to as_of + 1 day."""
    return p.start, min(p.end, as_of + timedelta(days=1))


# Catalogue capabilities for this release, not dates inferred from the clock.
# semantic_snapshots contains these eleven quarterly observations; current case
# and account DPD have no historical state. Cure uses dated case events instead.
ROLL_SNAPSHOTS = frozenset(date.fromisoformat(value) for value in (
    "2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
    "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30",
))
CASE_EVENTS_START = date(2016, 10, 2)


def _population_problem(question: str) -> str | None:
    """Reject entity identities the portfolio templates cannot bind as filters."""
    entity = r"(?:case|customer|golden_customer|crm_customer|borrower|account|agent|loan|contact|promise)"
    identity = re.search(
        r"\bCS-\d{4}-\d{5,7}\b|"
        r"\b(?:GC|G|CUST|CUSTOMER|CRM|ACC|ACCOUNT|AG|AGENT|LOAN|LN|CARD|PTP|CONTACT)[_-][\w-]+\b|"
        r"\b(?:GC|G|CUST|ACC|AG)\d+\b|"
        rf"\b{entity}[ _](?:id|identifier|key|ref|number)\b|"
        rf"\b{entity}\s*(?:[=:#]|named\b|called\b)\s*\S+|"
        rf"\b(?:for|of|from|to|on|with)\s+(?:the\s+)?{entity}\s+"
        r"(?:['\"]|(?=[\w-]*\d)[\w-]+)|"
        rf"\b(?:assigned to|handled by|owned by)\s+(?:{entity}\s+)?\S+",
        question, re.I,
    )
    # A singular entity followed by a value selects an identity, regardless of
    # name capitalisation. Generic entity attributes/populations are not names.
    attributes = {"age", "province", "site", "product", "queue", "status", "accounts", "cases", "contacts"}
    named = any(m[1].lower() not in attributes and m[1].lower() not in MONTHS
                for m in re.finditer(rf"\b(?:for|of|from|to|on|with)\s+(?:the\s+)?{entity}\s+"
                                     r"([\w-]+)\b", question, re.I))
    if identity or named:
        return "Named-entity filters are not supported by this portfolio metric; the requested population cannot be preserved."
    return None


def _period_problem(question: str, metric_id: str, periods: list[Period], as_of: date) -> str | None:
    if metric_id in {"delinquency_rate", "average_dpd"}:
        # A current-month request may use the current snapshot, but a past
        # month/day or an interval spanning history cannot be substituted.
        current_month = date(as_of.year, as_of.month, 1)
        if any(not (p.start == as_of and p.end == as_of + timedelta(days=1)
                    or p.start == current_month and p.start <= as_of < p.end
                    and (p.end - p.start).days <= 31) for p in periods):
            return f"{metric_id.replace('_', ' ')} is available only at the current snapshot ({as_of}); historical periods cannot be reconstructed."
        dates = [date.fromisoformat(m[0]) for m in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b", question)]
        for m in re.finditer(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})\b", question, re.I):
            dates.append(date(int(m[3]), MONTHS[m[2].lower()], int(m[1])))
        if any(d != as_of for d in dates) or re.search(r"\b(?:last|previous|prior)\s+(?:day|week|month|quarter|year)\b|\byesterday\b", question, re.I):
            return f"Only the current snapshot ({as_of}) is available; the requested historical or future snapshot is unsupported."
    elif metric_id == "roll_rate":
        if any(p.end != p.start + timedelta(days=1) or p.start not in ROLL_SNAPSHOTS or p.start > as_of for p in periods):
            return "Roll rate requires available quarterly month-end snapshots from December 2023 through June 2026, on or before the as-of date."
    elif metric_id == "cure_rate":
        if any(p.start < CASE_EVENTS_START or p.start > as_of for p in periods):
            return f"Cure event history starts on {CASE_EVENTS_START} and is observed only through {as_of}; the requested period is unsupported."
    return None


def build_call(question: str, metric_id: str, as_of: date) -> MetricCall | Unsupported:
    q = question.lower()
    if requests_contact_quantity(question) and metric_id != "contact_attempts_per_case":
        return Unsupported("An average count of contacts per case cannot be answered with a different quantity such as days past due.")
    if metric_id == "average_dpd" and requests_entity_count(question):
        return Unsupported("A count of cases or accounts cannot be answered with average days past due.")
    try:
        periods = parse_periods(question, as_of)
        period_problem = _period_problem(question, metric_id, periods, as_of)
    except ValueError:
        return Unsupported("The requested date is invalid; supply a valid reporting period.")
    if period_problem:
        return Unsupported(period_problem)
    dims, bad = _dimensions(question, metric_id)
    if bad:
        return Unsupported(bad)
    population_problem = _population_problem(question)
    if population_problem:
        return Unsupported(population_problem)
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
        # Measured 4 Oct: 418,319 snapshot rows date their month-end month-first (03/31/2026). Curation reads them;
        # a parser that only knows day-first drops them, and BQ-017's reference SQL does (accounts 733 vs 781 etc.).
        call.notes.append("Includes snapshot rows whose month-end date is written month-first (e.g. 03/31/2026); "
                          "they are 418,319 of 12,569,692 rows, so a count that skips them is lower.")
    elif metric_id == "average_dpd":
        call.parameters = {}
        call.notes.append("Open cases only, as of the as-of date.")
    elif metric_id == "payroll_delay_share":
        call.parameters = {"snapshot_date": as_of}
    else:
        return Unsupported("No parameter rules for this metric.")
    return call
