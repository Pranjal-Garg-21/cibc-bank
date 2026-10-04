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

import duckdb
import sqlglot
from sqlglot import exp

from trustissues.common.llm import LLMError

from .data.execute import QueryResult, QueryTimeout, execute
from .data.semantic import SCHEMAS as _ALL_SCHEMAS, TEMPLATE_ONLY
from .data.validator import SAFE_FUNCTIONS, QueryPolicy, ValidationError, validate
from .join_context import prompt_join_context

# The tables the model may see and query: everything but the template-only ones (gold.semantic_assignments).
SCHEMAS = {t: cols for t, cols in _ALL_SCHEMAS.items() if t not in TEMPLATE_ONLY}

PROMPT_VERSION = "ask_sql_v2"

TABLE_NOTES = {
    "gold.semantic_cases": "one row per collections case. Open case = outcome IS NULL. reported_bucket is the STORED "
                           "delinquency bucket (use it for 'current bucket'); canonical_bucket is recomputed from current_dpd. "
                           "total_overdue is the amount overdue on the case; days_to_cure is days from opening to cure_date.",
    "gold.semantic_promises": "one row per promise to pay. Count only ptp_status IN ('kept','partially_kept','broken') as resolved. "
                              "ptp_channel is the channel the promise was taken on ('call', 'ivr', 'web', 'app', 'sms_reply').",
    "gold.semantic_contacts": "one row per contact attempt. channel is 'call', 'sms', 'email', ...; 'calls' means channel = 'call'. "
                              "direction is 'outbound' or 'inbound'; contact_ts_local is local time; "
                              "within_permitted_hours_flag is supplied by the source (use it, do not recompute); "
                              "dispute_raised_flag marks a dispute raised on the contact; payment_link_sent_flag marks a contact "
                              "that carried a payment link and link_clicked_flag whether that link was clicked.",
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
12. If a word in the question could mean more than one thing in this schema (for example 'late stage' could be a queue or
    a delinquency bucket range), choose the most literal reading and say which in "assumptions", in one short sentence.
    Use "" when nothing was ambiguous.

Schema:
{schema}

Reply with JSON only: {{"sql": string or null, "reason": string, "assumptions": string}}"""


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
    first = re.sub(r"'[^']*'", "'?'", message.splitlines()[0])
    return re.sub(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", "N", first)[:200]  # a number in an error may be a data value


# Words in a question that imply a filter on gold.semantic_contacts. A query that reads the table without it counts
# the wrong thing and still looks plausible, so it is sent back for repair instead of being run.
IMPLIED_FILTERS = (
    (r"\bcalls?\b(?!\s*cent)", "channel", "call", "calls"),
    (r"\b(?:sms|text messages?)\b", "channel", "sms", "SMS or text messages"),
    (r"\bemails?\b", "channel", "email", "emails"),
    (r"\boutbound\b", "direction", "outbound", "outbound"),
    (r"\binbound\b", "direction", "inbound", "inbound"),
)


def _conjuncts(node) -> list:
    """The top-level AND terms of a condition; anything under an OR or NOT is not a term that must hold."""
    node = node.this if isinstance(node, exp.Where) else node
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node]


def _filters_on(sql: str, column: str, value: str) -> bool:
    """True when some WHERE clause requires `column = value` (or IN (... value ...)) as a top-level AND term."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return bool(re.search(rf"\b{column}\b[\"\s]*(?:=\s*'{value}'|in\s*\([^)]*'{value}')", sql, re.I))
    for where in tree.find_all(exp.Where):
        for term in _conjuncts(where):
            if isinstance(term, exp.EQ) and isinstance(term.this, exp.Column) and term.this.name == column \
                    and isinstance(term.expression, exp.Literal) and term.expression.this == value:
                return True
            if isinstance(term, exp.In) and isinstance(term.this, exp.Column) and term.this.name == column \
                    and len(term.expressions) == 1 and getattr(term.expressions[0], "this", None) == value:
                return True
    return False


def implied_filter_problem(question: str, sql: str) -> str | None:
    """A repair message when the question names a channel or direction that the query on contacts does not require.

    The filter must be a top-level AND term of a WHERE clause: `(channel = 'call' OR 1 = 1)` does not count.
    This is still a heuristic about wording, not a proof that the query answers the question."""
    if "semantic_contacts" not in sql.lower():
        return None
    for pattern, column, value, word in IMPLIED_FILTERS:
        if re.search(pattern, question, re.I) and not _filters_on(sql, column, value):
            return f"The question asks about {word} but the query does not filter {column} = '{value}'"
    return None


def column_hint(err: str) -> str:
    """When a column was not found, say which approved table has it, so the repair can join instead of guessing."""
    m = re.search(r"Unknown column: (\w+)|Column '(\w+)' could not be resolved", err)
    if not m:
        return ""
    name = m[1] or m[2]
    owners = [t for t, cols in SCHEMAS.items() if name in cols]
    if not owners:
        return ""
    return (f"Hint: column {name} is not on the table you used; it is on {', '.join(owners)}. "
            "Join that table on a key both tables have (case_id, golden_customer_id or account_id).\n")


# Label columns with a handful of values. A filter on a value that is not among them returns an empty or zero
# result that reads like a fact ("0 accounts in default"), so the value is checked before the query runs.
LABEL_COLUMNS = frozenset({
    "reported_bucket", "canonical_bucket", "queue", "outcome", "primary_product", "product", "product_code", "channel",
    "direction", "province", "agent_site", "ptp_status", "account_status", "assignment_method", "test_cell", "ptp_channel"})


def _distinct(policy: QueryPolicy, con, database, table: str, column: str) -> set:
    """The label values this user's policy lets them see: the lookup is an ordinary validated query, so it carries the
    same row filters, as-of limit and column rules as the question itself. A table or column the role may not read
    raises ValidationError and the check is skipped, so nothing outside the role's view reaches a prompt."""
    checked = validate(f"SELECT {column} AS v, COUNT(*) AS n FROM {table} WHERE {column} IS NOT NULL GROUP BY {column}", policy)
    return {r[0] for r in execute(checked, None, con=con, database=database).rows}


def unknown_label(sql: str, policy: QueryPolicy, con=None, database=None) -> str | None:
    """A repair message when the query filters a label column on a value the column never holds."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
        tables = {f"gold.{t.name}" for t in tree.find_all(exp.Table) if f"gold.{t.name}" in SCHEMAS}
        checks = []
        for node in tree.find_all(exp.EQ, exp.In):
            col = node.this if isinstance(node.this, exp.Column) else None
            if col is None or col.name not in LABEL_COLUMNS:
                continue
            lits = [node.expression] if isinstance(node, exp.EQ) else list(node.expressions)
            checks += [(col.name, l.this) for l in lits if isinstance(l, exp.Literal) and l.is_string]
        for name, value in checks:
            owners = [t for t in tables if name in SCHEMAS[t]]
            if not owners:
                continue
            valid = set().union(*(_distinct(policy, con, database, t, name) for t in owners))
            if valid and value not in valid:
                shown = ", ".join(sorted(map(str, valid))[:12])
                return f"The value '{value}' never occurs in {name}. Its values are: {shown}. Use one of them, or say the question cannot be answered"
    except Exception:  # a check that cannot run must never block a question
        return None
    return None


@dataclass
class FallbackResult:
    sql: str | None
    result: QueryResult | None
    reason: str = ""
    attempts: int = 0
    assumptions: str = ""  # how the model resolved an ambiguous word in the question, in its own words


def _generate(llm, system: str, user: str, ctx: dict) -> tuple[str | None, str, str]:
    res = llm.complete(system=system, user=user, prompt_version=PROMPT_VERSION, tier="answer", json_output=True,
                       max_tokens=1200, audit_ctx=ctx)
    data = res.data if isinstance(res.data, dict) else {}
    sql = data.get("sql")
    note = data.get("assumptions")
    note = " ".join(note.split())[:300] if isinstance(note, str) else ""
    return (sql.strip().rstrip(";") if isinstance(sql, str) and sql.strip() else None), str(data.get("reason", "")), note


def run_fallback(question: str, *, llm, policy: QueryPolicy, as_of: date, con=None, database=None,
                 user_id: str | None = None) -> FallbackResult:
    system = build_system(as_of) + "\n\n" + prompt_join_context(question)
    # The as-of limits are added to whatever the role already requires for a table, never in place of it.
    trusted = dict(policy.required_filters)
    for table, preds in as_of_filters(as_of).items():
        trusted[table] = tuple(trusted.get(table, ())) + tuple(preds)
    tables = {t: cols for t, cols in policy.tables.items() if t not in TEMPLATE_ONLY}
    policy = dataclasses.replace(policy, tables=tables, required_filters=trusted)
    ctx = {"kind": "sql", "input_text": question, "user_role": policy.role, "user_id": user_id, "sources": []}
    user = f"Question: {question}"
    try:
        sql, reason, assumed = _generate(llm, system, user, ctx)
    except LLMError as e:
        return FallbackResult(None, None, f"the language model is unavailable ({type(e).__name__})", 1)
    attempts, last_sql = 1, sql
    while True:
        if sql is None:
            return FallbackResult(last_sql, None, reason or "the model could not express this question over the approved tables", attempts)
        try:
            problem = implied_filter_problem(question, sql) or unknown_label(sql, policy, con, database)
            if problem:
                raise ValueError(problem)
            checked = validate(sql, policy)
            return FallbackResult(checked.sql, execute(checked, None, con=con, database=database), "", attempts, assumed)
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
        hint += column_hint(err)
        try:
            sql, reason, assumed = _generate(llm, system, f"Question: {question}\n\nYour previous SQL failed.\nSQL: {sql}\nError: {err}\n"
                                    f"{hint}Return a corrected query in the same JSON shape.", ctx)
        except LLMError as e:
            return FallbackResult(last_sql, None, f"the language model is unavailable ({type(e).__name__})", attempts)
