"""Generated-SQL fallback for questions no certified metric covers.

The model writes one SELECT from a schema description only (names, types, notes; never row data). Everything it
writes goes through the same validator and read-only executor as certified queries, so it cannot widen access.
One repair attempt with the error message, then an explicit cannot-answer. Results are labelled uncertified.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import date, timedelta

from trustissues.common.llm import LLMError

from .data.execute import QueryResult, QueryTimeout, execute
from .data.semantic import SCHEMAS
from .data.validator import SAFE_FUNCTIONS, QueryPolicy, ValidationError, validate
from .join_context import prompt_join_context

PROMPT_VERSION = "ask_sql_v1"

TABLE_NOTES = {
    "gold.semantic_cases": "one row per collections case. Open case = outcome IS NULL. reported_bucket is the STORED "
                           "delinquency bucket (use it for 'current bucket'); canonical_bucket is recomputed from current_dpd.",
    "gold.semantic_promises": "one row per promise to pay. Count only ptp_status IN ('kept','partially_kept','broken') as resolved.",
    "gold.semantic_contacts": "one row per contact attempt. channel is 'call', 'sms', 'email', ...; 'calls' means channel = 'call'. "
                              "direction is 'outbound' or 'inbound'; contact_ts_local is local time; "
                              "within_permitted_hours_flag is supplied by the source (use it, do not recompute); "
                              "dispute_raised_flag marks a dispute raised on the contact.",
    "gold.semantic_accounts": "one row per account at snapshot_date (current state only).",
    "gold.semantic_snapshots": "month-end history per account.",
    "gold.semantic_payroll": "payroll deposit timing per customer.",
}

SYSTEM = """You write one DuckDB SELECT statement that answers a collections-analytics question.

Rules:
1. Use ONLY these tables and columns. Never invent a column. Qualify every table as gold.<table>.
2. One SELECT only. No writes, no set operations (UNION), no recursive queries, no table functions.
3. Allowed functions only: {functions}. Use CASE for conditionals and CAST(x AS DATE) for dates.
4. Put literal values straight into the SQL. Use half-open date ranges: col >= 'start' AND col < 'end'.
5. The as-of date is {as_of}. 'Today' means the as-of date. Rows after it are filtered out for you.
6. Report counts next to rates. Alias every output column with a short readable name.
7. Group labels go in the first column; numeric results follow. When grouping by a category, leave out rows where
   that category is NULL (no blank group).
8. To compare periods, label rows with CASE on the date and GROUP BY the label. UNION is not available. Label date ranges compactly, like '15-22 Sep' or '7-14 Sep' (no leading zeros).
9. Aggregate in the top-level SELECT. Do not wrap an aggregate query in an outer SELECT or subquery.
10. 'Current bucket' always means the stored column reported_bucket, never canonical_bucket.
11. If the schema cannot answer the question, return {{"sql": null, "reason": "<why>"}}.

Schema:
{schema}

Reply with JSON only: {{"sql": string or null, "reason": string}}"""


def _schema_text() -> str:
    out = []
    for table, cols in SCHEMAS.items():
        out.append(f"{table}: {TABLE_NOTES.get(table, '')}")
        out.append("  " + ", ".join(f"{c} {t}" for c, t in cols.items()))
    return "\n".join(out)


def build_system(as_of: date) -> str:
    return SYSTEM.format(functions=", ".join(sorted(SAFE_FUNCTIONS)), as_of=as_of.isoformat(), schema=_schema_text())


def as_of_filters(as_of: date) -> dict[str, tuple[str, ...]]:
    """Trusted predicates added inside every scan: no row dated after the as-of date can be read."""
    end = (as_of + timedelta(days=1)).isoformat()
    return {
        "gold.semantic_contacts": (f"coalesce(contact_ts_local, TIMESTAMP '1900-01-01') < CAST('{end}' AS TIMESTAMP)",),
        "gold.semantic_promises": (f"coalesce(ptp_created_ts, TIMESTAMP '1900-01-01') < CAST('{end}' AS TIMESTAMP)",),
        "gold.semantic_cases": (f"coalesce(case_open_date, DATE '1900-01-01') < CAST('{end}' AS DATE)",),
        "gold.semantic_accounts": (f"coalesce(snapshot_date, DATE '1900-01-01') < CAST('{end}' AS DATE)",),
        "gold.semantic_snapshots": (f"coalesce(snapshot_date, DATE '1900-01-01') < CAST('{end}' AS DATE)",),
        "gold.semantic_payroll": (f"coalesce(snapshot_date, DATE '1900-01-01') < CAST('{end}' AS DATE)",),
    }


def _redact(message: str) -> str:
    """Error text goes back to the model: keep the shape, drop anything quoted (it may be a data value)."""
    return re.sub(r"'[^']*'", "'?'", message.splitlines()[0])[:200]


@dataclass
class FallbackResult:
    sql: str | None
    result: QueryResult | None
    reason: str = ""
    attempts: int = 0


def _generate(llm, system: str, user: str, ctx: dict) -> tuple[str | None, str]:
    res = llm.complete(system=system, user=user, prompt_version=PROMPT_VERSION, tier="answer", json_output=True,
                       max_tokens=1200, audit_ctx=ctx)
    data = res.data if isinstance(res.data, dict) else {}
    sql = data.get("sql")
    return (sql.strip().rstrip(";") if isinstance(sql, str) and sql.strip() else None), str(data.get("reason", ""))


def run_fallback(question: str, *, llm, policy: QueryPolicy, as_of: date, con=None, database=None,
                 user_id: str | None = None) -> FallbackResult:
    system = build_system(as_of) + "\n\n" + prompt_join_context(question)
    policy = dataclasses.replace(policy, required_filters={**policy.required_filters, **as_of_filters(as_of)})
    ctx = {"kind": "sql", "input_text": question, "user_role": policy.role, "user_id": user_id, "sources": []}
    user = f"Question: {question}"
    try:
        sql, reason = _generate(llm, system, user, ctx)
    except LLMError as e:
        return FallbackResult(None, None, f"the language model is unavailable ({type(e).__name__})", 1)
    attempts, last_sql = 1, sql
    while True:
        if sql is None:
            return FallbackResult(last_sql, None, reason or "the model could not express this question over the approved tables", attempts)
        try:
            checked = validate(sql, policy)
            return FallbackResult(checked.sql, execute(checked, None, con=con, database=database), "", attempts)
        except (ValidationError, QueryTimeout, ValueError) as e:
            err = f"{type(e).__name__}: {e}"
        except Exception as e:  # duckdb binder / conversion errors: also worth one repair, never a crash
            err = f"{type(e).__name__}: {_redact(str(e))}"
        if attempts >= 2:
            return FallbackResult(sql, None, f"the generated query was rejected after one repair: {err}", attempts)
        attempts += 1
        last_sql = sql
        hint = ""
        if "aggregates only" in err or "subqueries" in err:
            hint = ("Hint: write ONE flat SELECT with GROUP BY and ORDER BY in the same statement. "
                    "No outer SELECT, no FROM (SELECT ...), no UNION.\n")
        try:
            sql, reason = _generate(llm, system, f"Question: {question}\n\nYour previous SQL failed.\nSQL: {sql}\nError: {err}\n"
                                    f"{hint}Return a corrected query in the same JSON shape.", ctx)
        except LLMError as e:
            return FallbackResult(last_sql, None, f"the language model is unavailable ({type(e).__name__})", attempts)
