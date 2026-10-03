"""Fail-closed DuckDB SELECT validation and per-scan role scoping.

Policy is supplied by trusted access code, never by model-generated text. No warehouse is opened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
import yaml

from trustissues.common.config import CONFIG_DIR


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class RowScope:
    column: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class QueryPolicy:
    role: str
    tables: Mapping[str, Mapping[str, str]]
    row_scopes: Mapping[str, RowScope] = field(default_factory=dict)
    # Trusted SQL predicates for mandatory certified defaults; applied inside EVERY table scan.
    required_filters: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # (left table, left column, right table, right column), in either orientation.
    join_keys: frozenset[tuple[str, str, str, str]] = frozenset()
    row_cap: int = 500
    sensitivity_path: Path = CONFIG_DIR / "sensitivity.yaml"


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    scope_parameters: Mapping[str, object]
    row_cap: int


IDENTIFIER_COLUMNS = {"golden_customer_id", "case_id", "account_id", "ptp_id", "contact_id",
                      "source_key", "source_id", "recommendation_id", "agent_id", "assigned_agent_id"}
# Deterministic scalar/aggregate functions needed by certified templates. Unknown/UDF functions are denied.
SAFE_FUNCTIONS = {
    "COUNT", "SUM", "AVG", "MIN", "MAX", "ROUND", "COALESCE", "NULLIF", "CAST", "TRY_CAST",
    "CASE", "IF", "LEAST", "GREATEST", "DATE_DIFF", "DATEDIFF", "DATE_ADD", "DATEADD",
    "EXTRACT", "YEAR", "MONTH", "DAY", "HOUR", "ABS", "LOWER", "UPPER", "TRIM",
    "BOOL_OR", "BOOL_AND", "STRFTIME", "TIME_TO_STR", "TS_OR_DS_ADD", "TS_OR_DS_TO_DATE", "AND", "OR", "EXISTS",
}


def sensitive_columns(path: Path) -> set[str]:
    with path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    blocked = {c.lower() for cs in doc.get("tables", {}).values() for c, label in cs.items()
               if label != "none"}
    blocked.update(x.rsplit(".", 1)[-1].lower() for x in doc.get("proxy_columns", []))
    blocked.update({"age", "age_band", "fsa", "geo_lat", "geo_lon", "full_name", "date_of_birth",
                    "first_name", "last_name", "email", "phone", "postal_code", "national_id_hash"})
    return blocked


def _table_name(table: exp.Table) -> str:
    if table.catalog or not isinstance(table.this, exp.Identifier) or not table.db:
        raise ValidationError("Only explicitly qualified gold tables are allowed")
    return f"{table.db.lower()}.{table.name.lower()}"


def _lineage(scope: Scope, column: exp.Column) -> set[tuple[str, str]]:
    """Resolve a qualified derived column through CTE/subquery projections to base columns."""
    source = scope.sources.get(column.table)
    if isinstance(source, exp.Table):
        return {(_table_name(source), column.name.lower())}
    if isinstance(source, Scope):
        for projection in source.expression.selects:
            if projection.alias_or_name.lower() == column.name.lower():
                return set().union(*(_lineage(source, c) for c in projection.find_all(exp.Column)))
    raise ValidationError(f"Cannot resolve column lineage: {column.sql()}")


def validate(sql: str, policy: QueryPolicy) -> ValidatedQuery:
    if policy.role not in {"agent", "team_lead", "analyst"}:
        raise ValidationError("Unknown role")
    if not 1 <= policy.row_cap <= 10000:
        raise ValidationError("Row cap must be between 1 and 10000")
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.SqlglotError as e:
        raise ValidationError("SQL could not be parsed") from e
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValidationError("Exactly one SELECT is required (set operations are unsupported)")
    tree = statements[0]
    if any(not isinstance(s.expression, exp.Select) for s in traverse_scope(tree)):
        raise ValidationError("Only SELECT subqueries are supported")
    if any(node for node in tree.walk() if isinstance(node, (exp.DML, exp.DDL, exp.Into, exp.Command, exp.Lock))):
        raise ValidationError("SQL writes and locks are forbidden")
    if tree.args.get("with_") and tree.args["with_"].args.get("recursive"):
        raise ValidationError("Recursive CTEs are unsupported")
    for fn in tree.find_all(exp.Func):
        name = fn.name.upper() if isinstance(fn, exp.Anonymous) else fn.sql_name().upper()
        if name not in SAFE_FUNCTIONS:
            raise ValidationError(f"Function is not approved: {name}")

    blocked = sensitive_columns(policy.sensitivity_path)
    schema: dict = {"gold": {}}
    for table, columns in policy.tables.items():
        if not table.startswith("gold.") or table.count(".") != 1:
            raise ValidationError("Policies may expose only gold tables")
        schema["gold"][table.split(".")[1]] = {c: t for c, t in columns.items() if c.lower() not in blocked}

    # CTE references are not base tables. Inspect every scope so nested scans cannot bypass policy.
    scans = []
    for scope in traverse_scope(tree):
        for _, (_, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table):
                name = _table_name(source)
                if name not in policy.tables:
                    raise ValidationError(f"Table is not approved: {name}")
                scans.append((source, name))
    if not scans:
        raise ValidationError("Query must read an approved table")
    try:
        tree = qualify(tree, dialect="duckdb", schema=schema, infer_schema=False,
                       validate_qualify_columns=True, quote_identifiers=True)
    except sqlglot.errors.SqlglotError as e:
        raise ValidationError(f"Unknown, ambiguous or sensitive column: {e}") from e

    scopes = traverse_scope(tree)
    for scope in scopes:
        for column in scope.columns:
            if any(c in blocked for _, c in _lineage(scope, column)):
                raise ValidationError("Sensitive column is forbidden")
        for join in scope.expression.find_all(exp.Join):
            # Only joins owned by this SELECT; descendant joins are checked in their own scopes.
            if join.find_ancestor(exp.Select) is not scope.expression:
                continue
            on = join.args.get("on")
            if on is None or join.args.get("using"):
                raise ValidationError("Joins require approved equality keys")
            predicates = list(on.flatten()) if isinstance(on, exp.And) else [on]
            for predicate in predicates:
                if not isinstance(predicate, exp.EQ) or not all(isinstance(x, exp.Column) for x in (predicate.left, predicate.right)):
                    raise ValidationError("Join predicates must be equality comparisons of approved keys")
                left, right = _lineage(scope, predicate.left), _lineage(scope, predicate.right)
                if len(left) != 1 or len(right) != 1:
                    raise ValidationError("Ambiguous join lineage")
                a, b = next(iter(left)), next(iter(right))
                key = (*a, *b)
                if key not in policy.join_keys and (*b, *a) not in policy.join_keys:
                    raise ValidationError(f"Join edge is not approved: {key}")

    if policy.role == "team_lead":
        if tree.find(exp.Window) or not (tree.args.get("group") or any(p.find(exp.AggFunc) for p in tree.selects)):
            raise ValidationError("Team leads may query aggregates only")
        root = scopes[-1]
        for column in tree.args.get("group", exp.Group()).find_all(exp.Column):
            if any(c in IDENTIFIER_COLUMNS for _, c in _lineage(root, column)):
                raise ValidationError("Team-lead aggregates cannot group by entity identifiers")
    if policy.role != "agent":
        root = scopes[-1]
        for projection in tree.selects:
            for column in projection.find_all(exp.Column):
                if any(c in IDENTIFIER_COLUMNS for _, c in _lineage(root, column)):
                    # Aggregated IDs (COUNT DISTINCT case_id) are useful and do not reveal identities.
                    if not isinstance(column.find_ancestor(exp.AggFunc), exp.Count):
                        raise ValidationError("Entity identifiers cannot be displayed for this role")

    # Wrap physical scans, not the outer result: WHERE/OR/subqueries cannot remove the trusted scope.
    index = 0
    parameters = {}
    for scope in scopes:
        for alias, (_, source) in list(scope.selected_sources.items()):
            if not isinstance(source, exp.Table):
                continue
            name = _table_name(source)
            predicates = []
            if policy.role == "agent":
                row_scope = policy.row_scopes.get(name)
                if row_scope is None or row_scope.column not in schema["gold"][source.name]:
                    raise ValidationError(f"Missing authorised row scope for {name}")
                if row_scope.values:
                    slots = []
                    for value in row_scope.values:
                        key = f"__ti_scope_{index}"
                        index += 1
                        parameters[key] = value
                        slots.append(exp.Placeholder(this=key))
                    predicates.append(exp.In(this=exp.column(row_scope.column), expressions=slots))
                else:
                    predicates.append(exp.false())
            for predicate in policy.required_filters.get(name, ()):
                predicates.append(sqlglot.parse_one(predicate, read="duckdb"))
            if predicates:
                base = source.copy()
                base.set("alias", None)
                inner = exp.select("*").from_(base)
                inner = inner.where(exp.and_(*predicates))
                source.replace(inner.subquery(alias=alias))

    if any(p.name.startswith("__ti_scope_") for p in statements[0].find_all(exp.Placeholder)):
        # The original tree may share rewritten nodes; check ORIGINAL SQL independently for collisions.
        original = sqlglot.parse_one(sql, read="duckdb")
        if any(p.name.startswith("__ti_scope_") for p in original.find_all(exp.Placeholder)):
            raise ValidationError("Reserved parameter prefix")
    limit = tree.args.get("limit")
    if limit is not None:
        value = limit.expression
        if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) < 0:
            raise ValidationError("LIMIT must be a non-negative integer literal")
        cap = min(int(value.this), policy.row_cap)
    else:
        cap = policy.row_cap
    tree = tree.limit(cap)
    return ValidatedQuery(tree.sql(dialect="duckdb"), parameters, policy.row_cap)
