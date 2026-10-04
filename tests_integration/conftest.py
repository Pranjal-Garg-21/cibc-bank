"""Read-only published-warehouse fixtures; model tests require RUN_LLM_TESTS=1.

Run from the repository root with PYTHONPATH=src .venv/bin/python -m pytest
tests_integration -q, optionally prefixed by RUN_LLM_TESTS=1. A missing warehouse
or gold schema skips this folder; a writer lock fails rather than hiding it.
"""
from datetime import date
import os
from pathlib import Path

import duckdb
import pytest


ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 9, 28)
WAREHOUSE = ROOT / "warehouse/trustissues.duckdb"


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: real configured model; enabled only by RUN_LLM_TESTS=1")


def pytest_collection_modifyitems(items):
    if os.environ.get("RUN_LLM_TESTS") == "1":
        return
    for item in items:
        if Path(__file__).parent in Path(item.path).parents and item.get_closest_marker("llm"):
            item.add_marker(pytest.mark.skip(reason="Real-model integration tests require RUN_LLM_TESTS=1"))


@pytest.fixture(scope="session")
def warehouse():
    if not WAREHOUSE.is_file():
        pytest.skip(f"Published warehouse missing: {WAREHOUSE}; integration folder skipped")
    try:
        con = duckdb.connect(str(WAREHOUSE), read_only=True)
    except duckdb.IOException as error:
        pytest.fail(f"Cannot open warehouse read-only; stop the pipeline writer first: {error}", pytrace=False)
    try:
        if not con.execute("SELECT count(*) FROM information_schema.schemata WHERE schema_name='gold'").fetchone()[0]:
            pytest.skip("Published gold schema missing; integration folder skipped")
        # Connection-local limits only, below the configured 9 GB maximum.
        con.execute("SET memory_limit='4GB'")
        con.execute("SET threads=2")
        yield con
    finally:
        con.close()


@pytest.fixture(autouse=True)
def require_published_warehouse(warehouse):
    """Even model-skipped tests never create a replacement warehouse."""


@pytest.fixture(scope="session")
def as_of():
    return AS_OF


@pytest.fixture(scope="session")
def case_samples(warehouse):
    """Select IDs from independent assignment SQL, never from Ask's decisions."""
    assigned = warehouse.execute("""
        WITH active AS (
          SELECT case_id, agent_id FROM curated.case_assignment_history
          WHERE assigned_ts < TIMESTAMP '2026-09-29'
            AND (unassigned_ts IS NULL OR unassigned_ts >= TIMESTAMP '2026-09-29')
          QUALIFY row_number() OVER (PARTITION BY case_id ORDER BY assigned_ts DESC, assignment_id)=1
        )
        SELECT c.case_id, c.golden_customer_id, a.agent_id, g.team_lead_id
        FROM gold.case_customer c JOIN active a USING (case_id)
        JOIN curated.agents g ON g.agent_id=a.agent_id
        WHERE c.golden_customer_id IS NOT NULL AND g.team_lead_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM gold.customer_timeline t WHERE t.case_id=c.case_id
                      AND t.golden_customer_id=c.golden_customer_id AND t.event_type='transcript'
                      AND t.text IS NOT NULL AND t.event_ts < TIMESTAMP '2026-09-29')
        ORDER BY c.case_id LIMIT 1
    """).fetchone()
    unassigned = warehouse.execute("""
        SELECT c.case_id, c.golden_customer_id
        FROM gold.case_customer c JOIN curated.collections_cases k USING (case_id)
        WHERE c.golden_customer_id IS NOT NULL AND k.case_open_date < DATE '2026-09-29'
          AND (k.case_status='open' OR k.case_status_date >= DATE '2026-09-29')
          AND NOT EXISTS (SELECT 1 FROM curated.case_assignment_history a WHERE a.case_id=c.case_id
                          AND a.assigned_ts < TIMESTAMP '2026-09-29'
                          AND (a.unassigned_ts IS NULL OR a.unassigned_ts >= TIMESTAMP '2026-09-29'))
          AND NOT EXISTS (SELECT 1 FROM gold.nba_decisions n WHERE n.case_id=c.case_id
                          AND n.as_of_date=DATE '2026-09-28' AND n.agent_id IS NOT NULL)
          AND EXISTS (SELECT 1 FROM gold.customer_timeline t WHERE t.case_id=c.case_id
                      AND t.golden_customer_id=c.golden_customer_id AND t.event_type='transcript'
                      AND t.text IS NOT NULL AND t.event_ts < TIMESTAMP '2026-09-29')
        ORDER BY c.case_id LIMIT 1
    """).fetchone()
    assert assigned is not None and unassigned is not None, (
        f"Required real case fixtures: assigned_found={assigned is not None}, "
        f"unassigned_without_queue_found={unassigned is not None}")
    # Also exclude the NBA queue's owner, which independently permits agent access.
    other = warehouse.execute("""
        SELECT agent_id FROM curated.agents WHERE agent_id <> ?
          AND agent_id NOT IN (SELECT agent_id FROM gold.nba_decisions
                              WHERE case_id=? AND as_of_date=DATE '2026-09-28' AND agent_id IS NOT NULL)
        ORDER BY agent_id LIMIT 1
    """, [assigned[2], assigned[0]]).fetchone()
    assert other is not None, "Other-agent fixture count=0"
    missing = "CS-2099-9999999"
    assert warehouse.execute("SELECT count(*) FROM gold.case_customer WHERE case_id=?", [missing]).fetchone()[0] == 0, (
        "Synthetic missing-case fixture unexpectedly has count>0")
    return dict(assigned=assigned[0], golden=assigned[1], agent=assigned[2], lead=assigned[3],
                unassigned=unassigned[0], other_agent=other[0], missing=missing)


@pytest.fixture(scope="session")
def lead_team_samples(warehouse):
    """Registered in/out-of-team leads, excluding independent queue/work grants."""
    case = warehouse.execute("""
        WITH active AS (
          SELECT case_id,agent_id FROM curated.case_assignment_history
          WHERE assigned_ts < TIMESTAMP '2026-09-29'
            AND (unassigned_ts IS NULL OR unassigned_ts >= TIMESTAMP '2026-09-29')
          QUALIFY row_number() OVER (PARTITION BY case_id ORDER BY assigned_ts DESC,assignment_id)=1
        )
        SELECT c.case_id,c.golden_customer_id,g.team_lead_id
        FROM gold.case_customer c JOIN active a USING (case_id)
        JOIN curated.agents g ON g.agent_id=a.agent_id
        WHERE c.golden_customer_id IS NOT NULL AND g.team_lead_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM gold.customer_timeline t WHERE t.case_id=c.case_id
                      AND t.golden_customer_id=c.golden_customer_id AND t.event_type='transcript'
                      AND t.text IS NOT NULL AND t.event_ts < TIMESTAMP '2026-09-29')
          AND NOT EXISTS (
            SELECT 1 FROM curated.collections_cases k JOIN gold.case_customer cc USING (case_id)
            WHERE cc.golden_customer_id=c.golden_customer_id AND k.case_open_date < DATE '2026-09-29'
              AND (k.case_status='open' OR k.case_status_date >= DATE '2026-09-29')
              AND NOT EXISTS (SELECT 1 FROM curated.case_assignment_history h WHERE h.case_id=k.case_id
                              AND h.assigned_ts < TIMESTAMP '2026-09-29'
                              AND (h.unassigned_ts IS NULL OR h.unassigned_ts >= TIMESTAMP '2026-09-29')))
        ORDER BY c.case_id LIMIT 1
    """).fetchone()
    assert case is not None, "Assigned lead-case fixture count=0 after excluding unassigned-work grants"
    outside = warehouse.execute("""
        SELECT DISTINCT g.team_lead_id FROM curated.agents g
        WHERE g.team_lead_id IS NOT NULL AND g.team_lead_id <> ?
          AND NOT EXISTS (
            SELECT 1 FROM curated.case_assignment_history a JOIN gold.case_customer c USING (case_id)
            JOIN curated.agents own ON own.agent_id=a.agent_id
            WHERE c.golden_customer_id=? AND own.team_lead_id=g.team_lead_id
              AND a.assigned_ts < TIMESTAMP '2026-09-29'
              AND (a.unassigned_ts IS NULL OR a.unassigned_ts >= TIMESTAMP '2026-09-29'))
          AND NOT EXISTS (
            SELECT 1 FROM gold.nba_decisions n JOIN curated.agents own ON own.agent_id=n.agent_id
            WHERE n.golden_customer_id=? AND n.as_of_date=DATE '2026-09-28'
              AND own.team_lead_id=g.team_lead_id)
        ORDER BY g.team_lead_id LIMIT 1
    """, [case[2], case[1], case[1]]).fetchone()
    assert outside is not None, "Registered out-of-team lead fixture count=0"
    return dict(case_id=case[0], in_team=case[2], out_of_team=outside[0])


@pytest.fixture(scope="session")
def real_llm(tmp_path_factory):
    from trustissues.common.config import settings
    from trustissues.common.llm import LLM, ResponseCache, _load_dotenv
    _load_dotenv()
    if not os.environ.get(settings()["llm"]["api_key_env"]):
        pytest.skip("Configured model credential unavailable; no key is printed")
    cache = ResponseCache(tmp_path_factory.mktemp("integration_llm") / "responses.sqlite")
    client = LLM(cache=cache)
    yield client
    cache._db.close()


@pytest.fixture(scope="session")
def real_rag(real_llm, warehouse):
    from trustissues.ask.rag.policy_search import PolicyIndex
    from trustissues.ask.rag_handler import make_rag_handler
    return make_rag_handler(PolicyIndex.load(), real_llm, con=warehouse)
