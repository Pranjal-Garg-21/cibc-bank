"""Rules-first router. Decides the path only; execution and access checks happen in `service`."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .case_facts import FACT_WORDS
from .catalog import detect_metric, match_metric, plausible_metric_quantity, requests_record_set
from .requested_shape import requested_shape, requests_entity_count

CASE_ID = re.compile(r"\bCS-\d{4}-\d{5,7}\b", re.I)
POLICY_WORDS = (
    r"\b(casl|pipeda|do not call|dncl|consent|policy|regulat\w+|legal|law|sue|statute|grace period|"
    r"hardship plan|programme|program|consumer proposal|bankrupt\w*|insolvency|must the agent|should the agent|"
    r"what (?:must|should|can) (?:the )?agent|call attempts?|calling hours|permitted hours|"
    r"cease|harass\w*|third party|script|payment plan|job|employment|grace|do-not-call)\b"
)
CALL_WORDS = r"\b(call|conversation|transcript|spoke|said|says?|agree[ds]?|summari[sz]e)\b"
# Wording that asks for a rule or an instruction even when it contains analytic words.
POLICY_ASK = r"\b(how long|how many days|can we|can (?:the )?agent|should|must|is .{1,40} needed|(?:is|are|be) .{0,40}allowed|permitted to|what happens|does .{0,80}(?:prevent|prohibit|permit|allow))\b"
ANALYTIC = r"\b(how many|how much|how often|number of|count|share|rate|average|mean|fraction|proportion|total|percentage|compare|rank|distribution|breakdown|break down)\b"
DRIVER_WORDS = (r"^\s*why\b|\b(what (?:drove|is driving|explains|caused|is behind)|drivers? of|"
                r"why (?:did|has|have|was|were|is)\b.{0,80}\b(?:drop|fall|fell|dip|decline|decrease|rise|rose|increase|spike|change|improv\w*|worse\w*))")
COUNT_WORDS = r"\b(how many|how much|how often|number of|count|share|rate|average|mean|fraction|proportion|total|percentage|compare|rank|by)\b"


@dataclass(frozen=True)
class Route:
    path: str  # metric | rag_customer | case_facts | rag_corpus | sql | incident | cannot_answer
    metric_id: str | None = None
    case_id: str | None = None
    why: str = ""
    clarification: str | None = None


def route(question: str) -> Route:
    q = question.strip()
    case = CASE_ID.search(q)
    metric = detect_metric(q)
    shape = requested_shape(q)
    if shape in {"cost_per_rpc", "cost_per_contact"}:
        metric = shape
    # A case ID plus call wording is a customer-specific RAG question; an ID alone is not enough.
    if case and re.search(CALL_WORDS, q, re.I) and not metric:
        return Route("rag_customer", case_id=case[0].upper(), why="case id with call wording")
    # A case ID with anything but call wording is a question about that one case (status, balance, next action...).
    # Portfolio metrics cannot be filtered to one case, so a metric word beside a case ID does not make it a metric.
    if case and not re.search(DRIVER_WORDS, q, re.I) and (
            any(re.search(p, q, re.I) for p in FACT_WORDS.values()) or not re.search(POLICY_WORDS, q, re.I)):
        return Route("case_facts", case_id=case[0].upper(), why="case id with a question about the case itself")
    policy = re.search(POLICY_WORDS, q, re.I)
    if policy and (re.search(POLICY_ASK, q, re.I) or not re.search(ANALYTIC, q, re.I)):
        return Route("rag_corpus", why="policy wording")
    driver = re.search(DRIVER_WORDS, q, re.I)
    volume = re.search(r"(?:call|contact|outbound)\s+(?:volume|numbers?)|number of (?:calls|contacts)", q, re.I)
    if not driver and requests_record_set(q):
        return Route("sql", why="entity intersection or absence ratio requires its own population")
    if shape == "population" and not driver:
        return Route("sql", why="population count or distribution without a catalogue statistic")
    entity_count = requests_entity_count(q)
    if entity_count and not driver and metric == "average_dpd":
        return Route("sql", why="explicit entity count cannot be inferred as a mean")
    if metric is None and not (driver and volume) and re.search(COUNT_WORDS, q, re.I):
        match = match_metric(q)
        if match is not None:
            if entity_count and not driver and (match.metric_id == "average_dpd" or (
                    match.metric_id is None and match.candidates and match.candidates[0].metric_id == "average_dpd")):
                return Route("sql", why="explicit entity count cannot be inferred as a mean")
            if match.metric_id is None:
                if any(not plausible_metric_quantity(q, candidate.metric_id) for candidate in match.candidates[:2]):
                    return Route("cannot_answer", why="catalogue alternatives do not measure the requested quantity")
                return Route("cannot_answer", why="uncertain catalogue match", clarification=match.clarification)
            metric = match.metric_id
    if re.search(DRIVER_WORDS, q, re.I):
        if volume:
            return Route("drivers", metric_id="contact_volume", why="change in call volume")
        if metric:
            return Route("drivers", metric_id=metric, why=f"change in {metric}")
        return Route("incident", why="explanation needs metric evidence plus cited incident records")
    if metric == "cure_rate" and re.search(r"\b(?:average|mean|median)\s+(?:number\s+of\s+)?days\b|\bdays\s+to\s+cure\b", q, re.I):
        return Route("sql", why="days to cure is a duration, not the cure rate")
    if metric:
        return Route("metric", metric_id=metric, why=f"trigger for {metric}")
    if re.search(COUNT_WORDS, q, re.I):
        return Route("sql", why="analytic wording without a certified metric")
    return Route("cannot_answer", why="no rule matched")
