"""Rules-first router. Decides the path only; execution and access checks happen in `service`."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .catalog import detect_metric

CASE_ID = re.compile(r"\bCS-\d{4}-\d{5,7}\b", re.I)
POLICY_WORDS = (
    r"\b(casl|pipeda|do not call|dncl|consent|policy|regulat\w+|legal|law|sue|statute|grace period|"
    r"hardship plan|programme|program|consumer proposal|bankrupt\w*|insolvency|must the agent|should the agent|"
    r"what (?:must|should|can) (?:the )?agent|call attempts?|calling hours|permitted hours|"
    r"cease|harass\w*|third party|script)\b"
)
CALL_WORDS = r"\b(call|conversation|transcript|spoke|said|agree[ds]?|summari[sz]e)\b"
# Wording that asks for a rule or an instruction even when it contains analytic words.
POLICY_ASK = r"\b(how long|can we|can (?:the )?agent|should|must|is .{1,40} needed|allowed|permitted to|what happens)\b"
ANALYTIC = r"\b(what share|how many|rate|average|percentage|count of|compare|rank)\b"
COUNT_WORDS = r"\b(how many|number of|count|share|rate|average|total|percentage|compare|rank|by)\b"


@dataclass(frozen=True)
class Route:
    path: str  # metric | rag_customer | rag_corpus | sql | incident | cannot_answer
    metric_id: str | None = None
    case_id: str | None = None
    why: str = ""


def route(question: str) -> Route:
    q = question.strip()
    case = CASE_ID.search(q)
    metric = detect_metric(q)
    # A case ID plus call wording is a customer-specific RAG question; an ID alone is not enough.
    if case and re.search(CALL_WORDS, q, re.I) and not metric:
        return Route("rag_customer", case_id=case[0].upper(), why="case id with call wording")
    if metric:
        return Route("metric", metric_id=metric, why=f"trigger for {metric}")
    if re.search(r"\bwhy did\b.{0,60}\b(drop|fall|fell|dip|spike|rise|increase|decrease)\w*", q, re.I):
        return Route("incident", why="explanation needs metric evidence plus cited incident records")
    policy = re.search(POLICY_WORDS, q, re.I)
    if policy and (re.search(POLICY_ASK, q, re.I) or not re.search(ANALYTIC, q, re.I)):
        return Route("rag_corpus", why="policy wording")
    if re.search(COUNT_WORDS, q, re.I):
        return Route("sql", why="analytic wording without a certified metric")
    return Route("cannot_answer", why="no rule matched")
