import duckdb
import pytest

from trustissues.ask.data.semantic import SCHEMAS, create_semantic_fixtures
from trustissues.ask.data.schema_graph import SEMANTIC_JOIN_KEYS
from trustissues.ask.data.validator import QueryPolicy, RowScope, ValidationError, validate
from trustissues.ask.data.execute import execute


def policy(role="analyst", **kwargs):
    return QueryPolicy(role, SCHEMAS, join_keys=SEMANTIC_JOIN_KEYS, **kwargs)


@pytest.mark.parametrize("sql", [
    "DELETE FROM gold.semantic_cases", "SELECT queue FROM gold.semantic_cases; SELECT 1",
    "SELECT * FROM read_csv('https://example.com/data.csv')",
    "SELECT * FROM read_parquet('/etc/passwd')",
    "SELECT * FROM range(100)", "SELECT getenv('HOME') FROM gold.semantic_cases",
    "SELECT * FROM curated.customers", "SELECT unknown FROM gold.semantic_cases",
    "SELECT * FROM gold.semantic_cases UNION SELECT * FROM gold.semantic_cases",
    "SELECT queue INTO other FROM gold.semantic_cases",
    "WITH x AS (DELETE FROM gold.semantic_cases RETURNING queue) SELECT queue FROM gold.semantic_cases",
    "SELECT queue FROM gold.semantic_cases LIMIT $cap",
    "SELECT case_id FROM gold.semantic_cases", "SELECT count(*) FROM gold.semantic_cases c CROSS JOIN gold.semantic_cases b",
])
def test_rejects_unsafe_sql(sql):
    with pytest.raises(ValidationError):
        validate(sql, policy())


def test_pii_is_rejected_even_if_policy_exposes_it():
    p = QueryPolicy("analyst", {"gold.c360_customer": {"full_name": "VARCHAR", "segment": "VARCHAR"}})
    for sql in ["SELECT full_name FROM gold.c360_customer", "SELECT count(*) FROM gold.c360_customer WHERE full_name='x'",
                "WITH x AS (SELECT full_name AS innocent FROM gold.c360_customer) SELECT innocent FROM x"]:
        with pytest.raises(ValidationError):
            validate(sql, p)


def test_ctes_and_aliases_validate():
    result = validate("WITH x AS (SELECT queue AS q FROM gold.semantic_cases) SELECT q,count(*) FROM x GROUP BY q", policy())
    assert "LIMIT 500" in result.sql


@pytest.mark.parametrize("sql", [
    "SELECT case_id FROM gold.semantic_cases WHERE case_id='B' OR TRUE",
    "WITH x AS (SELECT case_id FROM gold.semantic_cases) SELECT case_id FROM x",
    "SELECT case_id FROM gold.semantic_cases WHERE EXISTS (SELECT 1 FROM gold.semantic_cases b WHERE b.case_id='B')",
])
def test_scope_cannot_be_removed_in_nested_queries(sql):
    c = duckdb.connect(":memory:")
    create_semantic_fixtures(c)
    c.execute("INSERT INTO gold.semantic_cases (case_id,queue) VALUES ('A','early'),('B','late')")
    p = policy("agent", row_scopes={"gold.semantic_cases": RowScope("case_id", ("A",))})
    result = execute(validate(sql, p), con=c)
    assert all(r[0] == "A" for r in result.rows)
    c.close()


def test_missing_or_empty_agent_scope():
    with pytest.raises(ValidationError):
        validate("SELECT queue FROM gold.semantic_cases", policy("agent"))
    q = validate("SELECT queue FROM gold.semantic_cases", policy("agent", row_scopes={
        "gold.semantic_cases": RowScope("case_id", ())}))
    assert "FALSE" in q.sql


def test_scope_parameters_cannot_be_overridden():
    c = duckdb.connect(":memory:")
    create_semantic_fixtures(c)
    q = validate("SELECT queue FROM gold.semantic_cases", policy("agent", row_scopes={
        "gold.semantic_cases": RowScope("case_id", ("A",))}))
    with pytest.raises(ValueError):
        execute(q, {"__ti_scope_0": "B"}, con=c)
    c.close()


def test_default_filter_cannot_be_or_ed_away():
    c = duckdb.connect(":memory:")
    create_semantic_fixtures(c)
    c.execute("INSERT INTO gold.semantic_contacts (channel) VALUES ('call'),('system')")
    q = validate("SELECT channel FROM gold.semantic_contacts WHERE TRUE OR channel='system'", policy(
        required_filters={"gold.semantic_contacts": ("channel <> 'system'",)}))
    assert execute(q, con=c).rows == (("call",),)
    c.close()


def test_row_cap_and_team_lead_aggregate():
    c = duckdb.connect(":memory:")
    create_semantic_fixtures(c)
    c.execute("INSERT INTO gold.semantic_cases (queue) VALUES ('a'),('b'),('c')")
    assert len(execute(validate("SELECT queue FROM gold.semantic_cases LIMIT 999", policy(row_cap=2)), con=c).rows) == 2
    with pytest.raises(ValidationError):
        validate("SELECT queue FROM gold.semantic_cases", policy("team_lead"))
    assert execute(validate("SELECT count(*) FROM gold.semantic_cases", policy("team_lead")), con=c).rows == ((3,),)
    c.close()


def test_join_edges_and_derived_identifier_leak():
    validate("SELECT c.queue,count(*) FROM gold.semantic_cases c JOIN gold.semantic_promises p ON c.case_id=p.case_id GROUP BY c.queue", policy())
    with pytest.raises(ValidationError):
        validate("SELECT count(*) FROM gold.semantic_cases c JOIN gold.semantic_promises p ON c.queue=p.queue", policy())
    with pytest.raises(ValidationError):
        validate("WITH x AS (SELECT case_id AS hidden FROM gold.semantic_cases) SELECT hidden FROM x", policy())
    with pytest.raises(ValidationError):
        validate("SELECT max(case_id) FROM gold.semantic_cases", policy())
    with pytest.raises(ValidationError):
        validate("SELECT queue,sum(current_dpd) OVER () FROM gold.semantic_cases", policy("team_lead"))
    with pytest.raises(ValidationError):
        validate("SELECT count(*) FROM gold.semantic_cases GROUP BY case_id", policy("team_lead"))


def test_identifier_used_only_in_an_aggregate_filter_is_allowed_but_not_shown():
    ok = ("SELECT count(*) FILTER (WHERE p.case_id IS NULL) AS none_yet, count(*) AS n "
          "FROM gold.semantic_cases c LEFT JOIN gold.semantic_promises p ON c.case_id=p.case_id")
    for role in ("analyst", "team_lead"):
        validate(ok, policy(role))
    for leak in ("SELECT max(case_id) FILTER (WHERE queue='late') FROM gold.semantic_cases",
                 "SELECT max(case_id) FROM gold.semantic_cases WHERE queue='late'",
                 "SELECT case_id FROM gold.semantic_cases WHERE queue='late'"):
        with pytest.raises(ValidationError):
            validate(leak, policy())
