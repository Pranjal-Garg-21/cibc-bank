"""Rules-first router. Decides the path only; execution and access checks happen in `service`."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .catalog import detect_metric, match_metric

CASE_ID = re.compile(r"\bCS-\d{4}-\d{5,7}\b", re.I)
POLICY_WORDS = (
    r"\b(casl|pipeda|do not call|dncl|consent|policy|regulat\w+|legal|law|sue|statute|grace period|"
    r"hardship plan|programme|program|consumer proposal|bankrupt\w*|insolvency|must the agent|should the agent|"
    r"what (?:must|should|can) (?:the )?agent|call attempts?|calling hours|permitted hours|"
    r"cease|harass\w*|third party|script|payment plan|job|employment|grace|do-not-call)\b"
)
CALL_WORDS = r"\b(call|conversation|transcript|spoke|said|agree[ds]?|summari[sz]e)\b"
# Wording that asks for a rule or an instruction even when it contains analytic words.
POLICY_ASK = r"\b(how long|how many days|can we|can (?:the )?agent|should|must|is .{1,40} needed|allowed|permitted to|what happens|does .{0,80}(?:prevent|prohibit|permit|allow))\b"
ANALYTIC = r"\b(what share|how many|rate|average|percentage|count of|compare|rank)\b"
DRIVER_WORDS = (r"\b(what (?:drove|is driving|explains|caused|is behind)|drivers? of|"
                r"why (?:did|has|have|was|were|is)\b.{0,80}\b(?:drop|fall|fell|dip|decline|decrease|rise|rose|increase|spike|change|improv\w*|worse\w*))")
COUNT_WORDS = r"\b(how many|how much|how often|number of|count|share|rate|average|mean|fraction|proportion|total|percentage|compare|rank|by)\b"


@dataclass(frozen=True)
class Route:
    path: str  # metric | rag_customer | rag_corpus | sql | incident | cannot_answer
    metric_id: str | None = None
    case_id: str | None = None
    why: str = ""
    clarification: str | None = None


def route(question: str) -> Route:
    q = question.strip()
    case = CASE_ID.search(q)
    metric = detect_metric(q)
    # A case ID plus call wording is a customer-specific RAG question; an ID alone is not enough.
    if case and re.search(CALL_WORDS, q, re.I) and not metric:
        return Route("rag_customer", case_id=case[0].upper(), why="case id with call wording")
    policy = re.search(POLICY_WORDS, q, re.I)
    if policy and (re.search(POLICY_ASK, q, re.I) or not re.search(ANALYTIC, q, re.I)):
        return Route("rag_corpus", why="policy wording")
    # Entity counts are not a rate merely because a delinquency bucket is named.
    entity_count = re.search(r"\b(how many|number of|count of|count)\b.{0,30}\b(?:open |collections )?cases\b", q, re.I)
    if entity_count and metric is None and not re.search(r"\b(disputes?|hardship|cure|attempts?|contact|times)\b", q, re.I):
        return Route("sql", why="case population count, not a catalogue rate")
    driver = re.search(DRIVER_WORDS, q, re.I)
    volume = re.search(r"(?:call|contact|outbound)\s+(?:volume|numbers?)|number of (?:calls|contacts)", q, re.I)
    if metric is None and not (driver and volume) and re.search(COUNT_WORDS, q, re.I):
        match = match_metric(q)
        if match is not None:
            if match.metric_id is None:
                return Route("cannot_answer", why="uncertain catalogue match", clarification=match.clarification)
            metric = match.metric_id
    if re.search(DRIVER_WORDS, q, re.I):
        if volume:
            return Route("drivers", metric_id="contact_volume", why="change in call volume")
        if metric:
            return Route("drivers", metric_id=metric, why=f"change in {metric}")
        return Route("incident", why="explanation needs metric evidence plus cited incident records")
    if metric:
        return Route("metric", metric_id=metric, why=f"trigger for {metric}")
    if re.search(COUNT_WORDS, q, re.I):
        return Route("sql", why="analytic wording without a certified metric")
    return Route("cannot_answer", why="no rule matched")
