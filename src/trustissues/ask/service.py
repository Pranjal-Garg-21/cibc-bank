"""The single Ask entry point, used by the app and the benchmark. No bypass: every call is checked."""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Callable

from trustissues.common.interfaces import RagRequest, RagResponse

from . import injection, refusal
from .access import AccessDenied, CaseResolver, check_case, check_path, query_policy
from .catalog import Unsupported, build_call
from .data.execute import QueryTimeout, execute
from .data.metric_templates import render_metric
from .data.validator import ValidationError, validate
from .router import route
from .sql_fallback import run_fallback
from .types import AnswerCard, UserContext

RagHandler = Callable[[RagRequest], RagResponse]


def fake_rag(request: RagRequest) -> RagResponse:
    """Stand-in until P4's handler is wired: says plainly that it has no sources."""
    return RagResponse(answer=None, not_found=True, coverage="RAG handler not connected")


def _csv(columns, rows) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([round(x, 4) if isinstance(x, float) else x for x in r])
    return out.getvalue().strip()


def ask(question: str, user: UserContext, *, as_of_date: date, con=None, database=None,
        rag: RagHandler = fake_rag, resolver: CaseResolver | None = None, llm=None) -> AnswerCard:
    def card(answer, path, **kw) -> AnswerCard:
        return AnswerCard(question, answer, path, as_of_date, **kw)

    flag = injection.screen(question)
    if flag:
        return card("I can't act on instructions embedded in a question.", "refused", refused=True,
                    refusal_reason=f"injection:{flag.kind}")
    hit = refusal.check(question)
    if hit:
        return card(f"I can't help with that. {hit.reason}", "refused", refused=True, refusal_reason=hit.category)

    r = route(question)
    try:
        if r.path == "metric":
            return _metric(question, user, as_of_date, r, con, database, card)
        if r.path == "rag_customer":
            if resolver is None:
                return card("Customer lookups are unavailable: no case resolver is connected.", "cannot_answer")
            owner = check_case(user, r.case_id, resolver)
            check_path(user, "rag_customer")
            resp = rag(RagRequest(question, "rag_customer", user.role, user.user_id, as_of_date, owner.golden_customer_id,
                                  case_id=r.case_id))
            return _rag_card(card, "rag_customer", resp)
        if r.path == "rag_corpus":
            check_path(user, "rag_corpus")
            resp = rag(RagRequest(question, "rag_corpus", user.role, user.user_id, as_of_date))
            return _rag_card(card, "rag_corpus", resp)
        if r.path == "sql":
            check_path(user, "sql")
            if llm is None:
                return card("This needs a query outside the certified metrics, and no language model is connected.",
                            "cannot_answer", notes=["uncertified path not available"])
            fb = run_fallback(question, llm=llm, policy=query_policy(user), as_of=as_of_date, con=con,
                              database=database, user_id=user.user_id)
            if fb.result is None:
                return card(f"I could not answer this reliably: {fb.reason}.", "cannot_answer", sql=fb.sql,
                            notes=["uncertified query rejected"])
            return card(_csv(fb.result.columns, fb.result.rows), "sql", sql=fb.sql, certified=False,
                        columns=fb.result.columns, rows=fb.result.rows, sources=["gold.semantic_*"],
                        notes=["Uncertified: this query was generated for this question and checked by the validator, "
                               "not by a reviewed metric definition."] + (["The first query needed one repair."] if fb.attempts > 1 else []))
        if r.path == "incident":
            check_path(user, "sql")
            return card("Explaining why a number changed needs incident and operations records, which are not "
                        "connected yet. A plain count by day can be requested instead.", "cannot_answer",
                        notes=["incident evidence unavailable"])
        return card("I can't tell what is being asked. Try naming a metric, a period, or a policy topic.", "cannot_answer")
    except AccessDenied as e:
        return card(str(e), "refused", refused=True, refusal_reason="access")


def _rag_card(card, path, resp: RagResponse) -> AnswerCard:
    if resp.not_found or not resp.answer:
        return card("The available sources do not contain an answer.", path,
                    notes=[resp.coverage] if resp.coverage else [])
    sources = [f"{c.kind}:{c.source_id}" + (f"#{c.section}" if c.section else "") for c in resp.citations]
    return card(resp.answer, path, sources=sources, notes=[resp.coverage] if resp.coverage else [])


def _metric(question, user, as_of, r, con, database, card) -> AnswerCard:
    check_path(user, "metric")
    call = build_call(question, r.metric_id, as_of)
    if isinstance(call, Unsupported):
        return card(call.reason, "cannot_answer", metric_id=r.metric_id)
    try:
        q = render_metric(call.metric_id, parameters=call.parameters, dimensions=call.dimensions,
                          cap_at=call.cap_at, variant=call.variant)
        checked = validate(q.sql, query_policy(user))
        res = execute(checked, q.parameters, con=con, database=database)
    except (ValueError, ValidationError, QueryTimeout) as e:
        return card(f"The certified query could not be run: {e}", "cannot_answer", metric_id=r.metric_id)
    rows = res.rows
    if "test_cell" in call.dimensions:  # show only the cells the question names; all cells if none are named
        i = call.dimensions.index("test_cell")
        named = tuple(r for r in rows if str(r[i]).lower() in question.lower())
        if named and len(named) < len(rows):
            rows = named
            call.notes.append("Showing only the strategy cells named in the question.")
    res = type(res)(res.columns, rows, res.elapsed_ms)
    if not res.rows:
        return card("No rows matched.", "metric", metric_id=r.metric_id, sql=q.sql, certified=True,
                    columns=res.columns, notes=call.notes)
    return card(_csv(res.columns, res.rows), "metric", metric_id=r.metric_id, sql=q.sql, parameters=q.parameters, certified=True,
                columns=res.columns, rows=res.rows, notes=call.notes, sources=["gold.semantic_*"])
