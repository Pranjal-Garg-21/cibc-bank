"""The single Ask entry point, used by the app and the benchmark. No bypass: every call is checked."""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import date, timedelta
from typing import Callable

from trustissues.common.interfaces import RagRequest, RagResponse

from . import disclosure, injection, refusal
from . import case_facts
from . import incidents
from .access import AccessDenied, CaseResolver, check_case, check_path, query_policy
from .catalog import Unsupported, build_call
from . import drivers as drivers_mod
from .asof import parse_periods
from .data.execute import QueryTimeout, execute
from .data.metric_templates import render_metric
from .data.validator import ValidationError, validate
from .router import Route, route
from . import llm_screen
from .llm_router import llm_route
from .sql_fallback import run_fallback
from .types import AnswerCard, UserContext

RagHandler = Callable[[RagRequest], RagResponse]


def _route(question: str, llm) -> Route:
    """Rules first; the model is asked when no rule placed the question or the matcher was torn between two metrics."""
    r = route(question)
    if llm is not None and r.path == "cannot_answer":
        return llm_route(question, llm) or r
    return r


def classify(question: str, llm=None):
    """What `ask()` would do with a question before touching any data: (outcome, metric_id).

    outcome is 'refused' (injection or refusal gate) or the router's path. Used by the router evaluation so a
    change to injection, refusal, catalogue or router shows up in one measurable number.
    """
    if injection.screen(question) or refusal.check(question):
        return "refused", None
    if llm is not None and route(question).path != "case_facts" and llm_screen.check(question, llm):
        return "refused", None
    r = _route(question, llm)
    return r.path, r.metric_id


def fake_rag(request: RagRequest) -> RagResponse:
    """Stand-in until P4's handler is wired: says plainly that it has no sources."""
    return RagResponse(answer=None, not_found=True, coverage="RAG handler not connected")


def _natural(value):
    """Sort key that puts '1-30' before '31-60' and '9' before '10', with missing values last."""
    if value is None:
        return (1, ())
    return (0, tuple((0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.findall(r"\d+|\D+", str(value))))


def stable_rows(columns, rows, *, by: int | None = None):
    """DuckDB returns grouped rows in a different order from run to run. Sort them by the group columns (those before
    the first numeric result column, or the first `by` columns) so the same question gives the same table every time."""
    rows = list(rows)
    if by is None:
        by = next((i for i, c in enumerate(columns) if c in ("metric_value", "numerator", "denominator")), 1)
    if by <= 0 or len(rows) < 2:
        return rows
    return sorted(rows, key=lambda r: tuple(_natural(v) for v in r[:by]))


def _after_as_of_note(question: str, as_of: date) -> list[str]:
    """Data cannot exist after the as-of date, so a period that starts later is empty by construction."""
    if any(p.start > as_of for p in parse_periods(question, as_of)):
        return [f"The period asked about starts after the as-of date ({as_of.isoformat()}); no data exists after it."]
    return []


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
        result = AnswerCard(question, answer, path, as_of_date, **kw)
        return disclosure.apply(result, format_rows=_csv) if path in {"metric", "drivers", "sql"} else result

    flag = injection.screen(question)
    if flag:
        return card("I can't act on instructions embedded in a question.", "refused", refused=True,
                    refusal_reason=f"injection:{flag.kind}")
    hit = refusal.check(question)
    if hit:
        return card(f"I can't help with that. {hit.reason}", "refused", refused=True, refusal_reason=hit.category)

    # The model screen is skipped for a question the rules place on one named case: that path returns only fixed fields
    # (never names, contact details or text from notes), so there is nothing for the screen to protect, and it
    # refused "who is the agent on case X" as a bulk identity pull. Injection and the refusal gate above still apply.
    flagged = llm_screen.check(question, llm) if llm is not None and route(question).path != "case_facts" else None
    if flagged:
        return card(f"I can't help with that. {flagged[1]}", "refused", refused=True, refusal_reason=f"model_screen:{flagged[0]}")

    r = _route(question, llm)
    if r.clarification:
        return card(r.clarification, "cannot_answer")
    try:
        if r.path == "metric":
            return _metric(question, user, as_of_date, r, con, database, card)
        if r.path == "rag_customer":
            if resolver is None:
                return card("Customer lookups are unavailable: no case resolver is connected.", "cannot_answer")
            check_path(user, "rag_customer")  # role first: a role that may not ask learns nothing about which cases exist
            owner = check_case(user, r.case_id, resolver)
            resp = rag(RagRequest(question, "rag_customer", user.role, user.user_id, as_of_date, owner.golden_customer_id,
                                  case_id=r.case_id))
            return _rag_card(card, "rag_customer", resp)
        if r.path == "case_facts":
            if resolver is None or con is None:
                return card("Case lookups are unavailable: no case resolver or warehouse is connected.", "cannot_answer")
            check_path(user, "case_facts")  # role first, then the case: a missing and a hidden case read the same
            owner = check_case(user, r.case_id, resolver)
            cf = case_facts.lookup(con, r.case_id, question, as_of=as_of_date, assigned_agent_id=owner.assigned_agent_id,
                                   user_id=user.user_id, role=user.role)
            return card(cf.answer, "case_facts", sql=cf.sql or None, columns=("field", "value"), rows=tuple(cf.rows),
                        sources=cf.sources, notes=["A direct lookup of this case's recorded fields; not a metric."] + cf.notes)
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
            rows = fb.result.rows if re.search(r"\border\s+by\b", fb.sql or "", re.I) else stable_rows(fb.result.columns, fb.result.rows, by=1)
            return card(_csv(fb.result.columns, rows), "sql", sql=fb.sql, certified=False,
                        columns=fb.result.columns, rows=rows, sources=["gold.semantic_*"],
                        notes=["Uncertified: this query was generated for this question and checked by the validator, "
                               "not by a reviewed metric definition."] + (["The first query needed one repair."] if fb.attempts > 1 else [])
                              + ([f"Interpretation: {fb.assumptions}"] if fb.assumptions else [])
                              + _after_as_of_note(question, as_of_date))
        if r.path == "drivers":
            return _drivers(question, user, as_of_date, r, con, database, card)
        if r.path == "incident":
            check_path(user, "sql")
            return card("Explaining why a number changed needs incident and operations records, which are not "
                        "connected yet. A plain count by day can be requested instead.", "cannot_answer",
                        notes=["incident evidence unavailable"])
        return card("I can't tell what is being asked. Try naming a metric, a period, or a policy topic.", "cannot_answer")
    except AccessDenied as e:
        return card(str(e), "refused", refused=True, refusal_reason="access")


def _rag_card(card, path, resp: RagResponse) -> AnswerCard:
    if resp.answer is None and resp.coverage:  # the path could not run (no model, no index, no history connected)
        return card(f"I could not answer this: {resp.coverage}.", path)
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
    res = type(res)(res.columns, stable_rows(res.columns, rows), res.elapsed_ms)
    if call.variant == "certification_pilot":
        effect = pilot_effect(res.rows)
        if effect:
            call.notes.insert(0, effect)
    if not res.rows:
        return card("No rows matched.", "metric", metric_id=r.metric_id, sql=checked.sql, parameters=q.parameters, certified=True,
                    columns=res.columns, notes=call.notes)
    return card(_csv(res.columns, res.rows), "metric", metric_id=r.metric_id, sql=checked.sql, parameters=q.parameters, certified=True,
                columns=res.columns, rows=res.rows, notes=call.notes, sources=["gold.semantic_*"])


def pilot_effect(rows) -> str | None:
    """Direction and size of the certification pilot's effect, with a two-proportion z-test (trusted arithmetic on the
    query's own counts). Rows: (agent_group, metric_value, numerator, denominator)."""
    by = {r[0]: (int(r[2]), int(r[3])) for r in rows}
    if "certified" not in by or "not_certified" not in by or not by["certified"][1] or not by["not_certified"][1]:
        return None
    (x1, n1), (x0, n0) = by["certified"], by["not_certified"]
    p1, p0, pooled = x1 / n1, x0 / n0, (x1 + x0) / (n1 + n0)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n0))
    z = (p1 - p0) / se if se else 0.0
    p_value = math.erfc(abs(z) / math.sqrt(2))
    word = "higher" if p1 > p0 else "lower" if p1 < p0 else "the same"
    verdict = ("unlikely to be chance (p < 0.05)" if p_value < 0.05 else
               "not distinguishable from chance at this sample size (p >= 0.05)")
    return (f"Direction of effect: certified agents' 30-day cure rate is {word}: {100 * p1:.2f}% ({x1:,} of {n1:,}) vs "
            f"{100 * p0:.2f}% ({x0:,} of {n0:,}) for agents not certified, a difference of {100 * (p1 - p0):+.2f} points; "
            f"two-proportion z = {z:.2f}, p = {p_value:.3f}, {verdict}.")


def _drivers(question, user, as_of, r, con, database, card) -> AnswerCard:
    check_path(user, "drivers")
    periods = parse_periods(question, as_of)
    if not periods:
        return card("Which period moved? e.g. 'in September 2026' or 'on 23-24 September 2026'.", "cannot_answer",
                    metric_id=r.metric_id)
    # two periods named: the later one is the period being explained
    ordered = sorted(periods[:2], key=lambda p: p.start)
    current = ordered[-1]
    explicit = ordered[0] if len(ordered) == 2 else None
    previous = drivers_mod.comparison_period(current, explicit)
    clamped = min(current.end, as_of + timedelta(days=1))  # no data exists after the as-of date
    cut = clamped < current.end
    current = type(current)(current.start, clamped, current.label)
    notes = [] if explicit else [f"Compared with {previous.label} ({previous.start} to {previous.end - timedelta(days=1)})."]
    if cut:
        notes.append(f"The period is cut at the as-of date ({as_of}).")
    params: dict = {}
    call = None
    if r.metric_id != "contact_volume":
        call = build_call(question, r.metric_id, as_of)
        if isinstance(call, Unsupported):
            return card(call.reason, "cannot_answer", metric_id=r.metric_id)
        params = call.parameters
    try:
        rep = drivers_mod.explain_metric(r.metric_id, params, current, previous, query_policy(user), con=con,
                                         database=database, variant=call.variant if call else "default",
                                         cap_at=call.cap_at if call else None)
    except (drivers_mod.DriversUnsupported, ValueError, ValidationError, QueryTimeout) as e:
        return card(f"I can't split this change reliably: {e}", "cannot_answer", metric_id=r.metric_id)
    cols = ("rank", "dimension", "value", "contribution", "rate_effect", "mix_effect", "share_of_change", "before", "after")
    rows = tuple((i + 1, s.dimension, s.value, round(s.contribution, 4), round(s.rate_effect, 4), round(s.mix_effect, 4),
                  None if s.share_of_change is None else round(s.share_of_change, 3),
                  None if s.before is None else round(s.before, 4), None if s.after is None else round(s.after, 4))
                 for i, s in enumerate(rep.segments))
    def num(x, sign=False):
        return f"{x:{'+' if sign else ''},.0f}" if abs(x) >= 1000 else f"{x:{'+' if sign else ''}.4g}"
    head = (f"summary: {r.metric_id.replace('_', ' ')} moved from {num(rep.total_before)} to {num(rep.total_after)} "
            f"({num(rep.change, True)}).")
    # an operational cause the segment split cannot see (BQ-030: the dialer outage behind the call drop)
    recorded = incidents.overlapping(current.start, current.end, as_of)
    if recorded:
        head += " " + " ".join(incidents.sentence(i) for i in recorded)
    return card(head + "\n" + _csv(cols, rows), "drivers", metric_id=r.metric_id, certified=r.metric_id != "contact_volume",
                columns=cols, rows=rows, sources=["gold.semantic_*"] + [f"incident:{i.doc_id}" for i in recorded],
                notes=notes + rep.notes,
                sql="\n\n".join(f"-- query {i + 1} of {len(rep.queries)}\n{q}" for i, q in enumerate(rep.queries)))
