"""Fixtures for the UI API tests: a tiny warehouse file in a temp folder (never the real one) and a TestClient."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import duckdb  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tiui.main import create_app  # noqa: E402
from tiui.settings import Settings  # noqa: E402

PAIR_1 = "a" * 32
PAIR_2 = "b" * 32
PAIR_3 = "d" * 32


def build_warehouse(path: Path) -> None:
    con = duckdb.connect(str(path))
    for s in ("curated", "gold"):
        con.execute(f"CREATE SCHEMA {s}")
    con.execute("""CREATE TABLE curated.agents (agent_id VARCHAR, agent_display_name VARCHAR, team_id VARCHAR,
        role VARCHAR, vulnerable_customer_certified BOOLEAN, status VARCHAR)""")
    con.execute("""INSERT INTO curated.agents VALUES
        ('A1', 'Agent One', 'T1', 'agent', false, 'active'),
        ('A2', 'Agent Two', 'T1', 'agent', true, 'active'),
        ('L1', 'Lead One', 'T1', 'team_lead', false, 'active'),
        ('Q1', 'QA One', 'T9', 'qa_analyst', false, 'active'),
        ('X1', 'Gone', 'T1', 'agent', false, 'terminated')""")
    con.execute("""CREATE TABLE gold.identity_review_queue (pair_id VARCHAR, node_a VARCHAR, node_b VARCHAR,
        reason VARCHAR, source_system_a VARCHAR, source_system_b VARCHAR, record_a VARCHAR, record_b VARCHAR,
        splink_probability DOUBLE, splink_explanation VARCHAR, llm_match BOOLEAN, llm_probability DOUBLE,
        llm_reason VARCHAR, llm_status VARCHAR, detail VARCHAR, evidence_key VARCHAR, status VARCHAR)""")
    con.execute(f"""INSERT INTO gold.identity_review_queue VALUES
        ('{PAIR_1}', 'crm|1', 'loan|9', 'ambiguous', 'crm', 'loan',
         '[SRC] crm [NAME] TEST PERSON [DOB] 1980-01-01 [PHONE] +14165550123,+14165550999 [POSTAL] M5V1A1',
         '[SRC] loan [NAME] TEST PERSON [DOB] 1980-01-01 [PHONE] +14165550123 [POSTAL] M5V1A1',
         0.97, 'name: exact | dob: exact', true, 0.9, 'Same person', NULL, 'splink 0.97', 'ek1', 'open'),
        ('{PAIR_2}', '-crm|2', 'card|3', 'grey_zone_no_llm', 'crm', 'card', '[SRC] crm [NAME] B', '[SRC] card [NAME] C',
         0.5, NULL, NULL, NULL, 'TypeError: secret key missing', 'unavailable', NULL, '=ek2', 'open'),
        ('{PAIR_3}', 'crm|5', 'loan|6', 'splink_ambiguous', 'crm', 'loan', '[SRC] crm', '[SRC] loan',
         0.99, NULL, NULL, NULL, NULL, NULL, NULL, 'ek3', 'open')""")
    con.execute("""CREATE TABLE gold._publish_log (attempt_ts TIMESTAMP, published BOOLEAN, failed_rules VARCHAR,
        warned_rules VARCHAR, tables VARCHAR, secs DOUBLE)""")
    con.execute("INSERT INTO gold._publish_log VALUES (TIMESTAMP '2026-10-03 18:00:00', true, '', 'Q6', 'x', 1.5)")
    con.execute("""CREATE TABLE gold._contract_results (rule_id VARCHAR, title VARCHAR, blocking BOOLEAN,
        violations BIGINT, total BIGINT, passed BOOLEAN, detail VARCHAR, checked_ts TIMESTAMP)""")
    con.execute("INSERT INTO gold._contract_results VALUES ('Q1', 'Unique ids', true, 0, 10, true, '', NULL)")
    con.close()


@pytest.fixture
def ui_settings(tmp_path) -> Settings:
    wh = tmp_path / "warehouse" / "test.duckdb"
    wh.parent.mkdir()
    build_warehouse(wh)
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "data_quality_report.md").write_text("# Data quality\n\nAll good.\n", encoding="utf-8")
    return Settings(demo_login=True, warehouse=wh, ui_audit=tmp_path / "warehouse" / "ui_audit.duckdb",
                    decisions_csv=tmp_path / "review" / "identity_decisions.csv", reports_dir=reports,
                    dist_dir=tmp_path / "no_dist")


@pytest.fixture
def client(ui_settings):
    with TestClient(create_app(ui_settings)) as c:
        yield c


@pytest.fixture
def make_client(ui_settings):
    def make(**changes):
        return TestClient(create_app(replace(ui_settings, **changes)))
    return make


def sign_in(c: TestClient, agent_id: str) -> str:
    """Sign in and return the CSRF token."""
    r = c.post("/api/session/login", json={"agent_id": agent_id})
    assert r.status_code == 200, r.text
    me = c.get("/api/session/me")
    assert me.status_code == 200, me.text
    return me.json()["csrf_token"]
