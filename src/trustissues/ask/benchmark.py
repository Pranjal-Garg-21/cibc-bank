"""Benchmark runner and development scorer.

The scorer is a development approximation, not the official grader: the release ships no grader or text rubric.
Tables are compared by dimension key (first column), never by row order. Text answers and citations are
reported as `review` for a human; unsupported tolerance formats are reported, never defaulted to a pass.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .drivers import format_params
from .service import ask
from .types import UserContext

# Stated benchmark identity: the normal pipeline with a real role, no bypass.
BENCHMARK_USER = UserContext("benchmark-lead", "team_lead")
SUBMISSION_COLUMNS = ("question_id", "answer", "sql_or_sources", "refused")


def read_questions(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def case_asker(con, question: str, as_of: date) -> tuple[UserContext | None, object]:
    """(user, resolver) for a question about a case: the agent the case is assigned to on the as-of date, else the
    agent today's queue sends to it. That is who would really ask it; a team lead outside the agent's team may not
    read the customer's history (P4's re-check). (None, resolver) when the question names no case or nobody holds it."""
    from .access import case_customer_resolver
    from .router import route

    resolver = case_customer_resolver(con, as_of)
    r = route(question)
    if r.path != "rag_customer" or not r.case_id:
        return None, resolver
    owner = resolver(r.case_id)
    agent = owner and (owner.assigned_agent_id or owner.queue_agent_id)
    return (UserContext(agent, "agent") if agent else None), resolver


def _has_case_customer(con) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'gold' "
                            "AND table_name = 'case_customer'").fetchone()[0])


def run(questions: list[dict], *, user: UserContext = BENCHMARK_USER, con=None, **ask_kwargs) -> list[dict]:
    """One submission row per input question, in input order. The router never sees metric_id or gold.

    `con` is passed to ask() for the queries. When it is a warehouse (it has gold.case_customer), case questions are
    also asked as the case's agent and resolved on it (`case_asker`); everything else is asked as `user`.
    """
    rows = []
    for q in questions:
        as_of = date.fromisoformat(q["as_of_date"])
        asker, kw = user, dict(ask_kwargs)
        if con is not None:
            kw["con"] = con
            if _has_case_customer(con):
                agent, kw["resolver"] = case_asker(con, q["question_text"], as_of)
                asker = agent or user
        card = ask(q["question_text"], asker, as_of_date=as_of, **kw)
        answer = card.answer
        if card.notes and card.path in ("metric", "sql"):
            answer += "\n" + "\n".join(f"note: {n}" for n in card.notes)
        cited = [s for s in card.sources if s.startswith("incident:")]  # a document cited beside the SQL (drivers)
        rows.append({"question_id": q["question_id"], "answer": answer,
                     "sql_or_sources": (card.sql + (f"\n-- parameters: {format_params(card.parameters)}" if card.parameters else "")
                                        + (f"\n-- sources: {'; '.join(cited)}" if cited else ""))
                           if card.sql else "; ".join(card.sources),
                     "refused": "true" if card.refused else "false"})
    return rows


def write_submission(rows: list[dict], path: Path) -> None:
    ids = [r["question_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate question_id in submission")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUBMISSION_COLUMNS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)


# ---- scoring ------------------------------------------------------------------------------------------------

@dataclass
class Result:
    question_id: str
    status: str  # pass | fail | review | unsupported_tolerance
    detail: list[str] = field(default_factory=list)


def parse_tolerance(text: str):
    """-> ('abs', n) | ('exact',) | ('review',) | ('unsupported', text)."""
    t = (text or "").strip().lower()
    if not t:
        return ("review",)
    if t == "exact" or t.startswith("exact count"):
        return ("exact",)
    if t == "direction of effect":
        return ("review",)
    m = re.fullmatch(r"±\s*([\d.]+)\s*(pts|cad|days)?", t)
    if m:
        return ("abs", float(m[1]))
    return ("unsupported", text)


def _table(text: str):
    """CSV text -> (header, rows) or None when the text is not a consistent table of 2+ columns."""
    lines = [ln for ln in text.strip().splitlines() if not ln.startswith("note:")]
    try:
        rows = list(csv.reader(io.StringIO("\n".join(lines))))
    except csv.Error:
        return None
    if len(rows) < 2 or len(rows[0]) < 1 or any(len(r) != len(rows[0]) for r in rows):
        return None
    return rows[0], rows[1:]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _key(x: str) -> str:
    n = _num(x)
    return str(int(n)) if n is not None and n == int(n) else x.strip().lower()


def compare_tables(gold_text: str, ours_text: str, tol) -> tuple[str, list[str]]:
    g, o = _table(gold_text), _table(ours_text)
    if g is None:
        return "review", ["gold is not a table"]
    if o is None:
        return "fail", ["answer is not a parseable table"]
    (gh, grows), (oh, orows) = g, o
    if len(gh) == 1 and len(grows) == 1:  # single scalar answer: compare against our first value
        gv, ov = _num(grows[0][0]), _num(orows[0][0])
        limit = {"exact": 0.0, "abs": tol[1] if tol[0] == "abs" else 0}.get(tol[0])
        if tol[0] == "unsupported":
            return "unsupported_tolerance", [f"tolerance not understood: {tol[1]!r}"]
        if gv is None or ov is None or limit is None:
            return "review", ["scalar needs review"]
        return ("pass" if abs(gv - ov) <= limit + 1e-9 else "fail"), [f"ours {ov:g} vs gold {gv:g}"]
    if tol[0] == "unsupported":
        return "unsupported_tolerance", [f"tolerance not understood: {tol[1]!r}"]
    detail, ok = [], True
    gold_map = {}
    for r in grows:
        if _key(r[0]) in gold_map:
            detail.append(f"duplicate gold key {r[0]}")
        gold_map[_key(r[0])] = r
    ours_map = {}
    for r in orows:
        k = _key(r[0])
        if k in ours_map:
            ok = False
            detail.append(f"duplicate row for {r[0]}")
        ours_map[k] = r
    for k in gold_map.keys() - ours_map.keys():
        ok = False
        detail.append(f"missing row {k}")
    for k in ours_map.keys() - gold_map.keys():
        ok = False
        detail.append(f"extra row {k}")
    limit = 0.0 if tol[0] == "exact" else tol[1] if tol[0] == "abs" else None
    if limit is None:
        return "review", ["no numeric tolerance"]
    for k in gold_map.keys() & ours_map.keys():
        gv, ov = _num(gold_map[k][1]) if len(gold_map[k]) > 1 else None, _num(ours_map[k][1]) if len(ours_map[k]) > 1 else None
        if gv is None or ov is None:
            ok = False
            detail.append(f"{k}: value missing")
        elif abs(gv - ov) > limit + 1e-9:
            ok = False
            detail.append(f"{k}: ours {ov:g} vs gold {gv:g} (limit {limit:g})")
        # group sizes are informational: reported, not scored
        if len(gold_map[k]) > 2 and len(ours_map[k]) > 2:
            gc, oc = _num(gold_map[k][-1]), _num(ours_map[k][-1])
            if gc is not None and oc is not None and gc != oc:
                detail.append(f"{k}: count differs (ours {oc:g}, gold {gc:g}) - not scored")
    return ("pass" if ok else "fail"), detail


def score(questions: list[dict], gold: dict[str, dict], submission: list[dict]) -> list[Result]:
    input_ids = [q["question_id"] for q in questions]
    sub = {r["question_id"]: r for r in submission}
    results = []
    if len(sub) != len(submission):
        results.append(Result("*", "fail", ["duplicate question_id in submission"]))
    for extra in set(sub) - set(input_ids):
        results.append(Result(extra, "fail", ["question_id not in the benchmark"]))
    for q in questions:
        qid = q["question_id"]
        g = gold.get(qid)
        if g is None:
            continue  # test split: no gold
        s = sub.get(qid)
        if s is None:
            results.append(Result(qid, "fail", ["no submission row"]))
            continue
        gold_answer = g["gold_answer"]
        if gold_answer.lower().startswith("refuse"):
            ok = s["refused"] == "true"
            results.append(Result(qid, "pass" if ok else "fail", [] if ok else ["should have refused"]))
            continue
        if s["refused"] == "true":
            results.append(Result(qid, "fail", ["refused a legitimate question"]))
            continue
        if _table(gold_answer) is None:
            results.append(Result(qid, "review", ["text answer: key facts and citations need human review"]))
            continue
        status, detail = compare_tables(gold_answer, s["answer"], parse_tolerance(q.get("tolerance", "")))
        results.append(Result(qid, status, detail))
    return results


def read_gold(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return {r["question_id"]: r for r in csv.DictReader(f)}
