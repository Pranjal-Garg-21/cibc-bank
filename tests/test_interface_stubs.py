"""The stubs load, have the agreed columns, and the decisioning view stays clean (contract rule 4)."""
import duckdb
import pytest

import dataclasses
import json
from datetime import date

from trustissues.common.fixtures import load_fixtures
from trustissues.common.interfaces import (
    PROMISE_OUTCOME_COLUMNS, DecisionCard, RagRequest, RagResponse, StubMismatch, apply_stubs)

PROTECTED = {
    "gender_code", "marital_status", "citizenship_status", "household_size", "dependants_count",
    "newcomer_program_flag", "newcomer_arrival_date", "accessibility_needs_flag", "accessibility_type",
    "vulnerability_type", "ccb_deposit_flag", "customer_accent_region",
}
PII = {
    "full_name", "first_name", "last_name", "date_of_birth", "dob_raw", "email", "primary_phone_e164",
    "address_line1", "postal_code", "national_id_hash", "geo_lat", "geo_lon", "age", "age_band", "fsa",
    "agent_accent_region", "interpreter_used",
}


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(":memory:")
    apply_stubs(c)
    apply_stubs(c)  # idempotent
    return c


def cols(con, table):
    return {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='gold' AND table_name=?", [table]).fetchall()}


def test_decisioning_customer_has_no_protected_or_pii(con):
    assert not (cols(con, "decisioning_customer") & (PROTECTED | PII))


def test_text_features_matches_i3(con):
    assert cols(con, "text_features") >= {
        "golden_customer_id", "source_id", "source_ts", "hardship_signal", "stated_delay_reason",
        "ptp_intent_strength", "dispute_mentioned", "evidence_sentence", "confidence", "prompt_version"}


def test_overrides_matches_i7(con):
    assert cols(con, "nba_overrides") == {"recommendation_id", "agent_id", "decision", "reason_code", "ts"}


def test_promises_enriched_has_point_in_time_key_and_label(con):
    assert {"ptp_created_ts", "label_broken", "golden_customer_id"} <= cols(con, "promises_enriched")


EXPECTED_TABLES = {
    "customer_xref", "c360_customer", "c360_account", "decisioning_customer", "promises_enriched",
    "customer_safeguards", "bucket_transitions", "customer_timeline", "text_features", "nba_decisions",
    "nba_overrides", "audit_log",
}


def test_exact_table_set(con):
    names = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='gold'").fetchall()}
    assert names == EXPECTED_TABLES


def test_decisioning_customer_is_history_with_both_timestamps(con):
    assert {"valid_from", "available_from"} <= cols(con, "decisioning_customer")
    assert "feature_ts" not in cols(con, "decisioning_customer")


def test_outcome_columns_exist_and_follow_the_marker(con):
    c = cols(con, "promises_enriched")
    assert PROMISE_OUTCOME_COLUMNS <= c
    order = [r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='gold' "
                                       "AND table_name='promises_enriched' ORDER BY ordinal_position").fetchall()]
    first_outcome = min(order.index(x) for x in PROMISE_OUTCOME_COLUMNS)
    assert not (set(order[:first_outcome]) & PROMISE_OUTCOME_COLUMNS)


def test_rag_dataclasses_carry_user_id_and_i6_fields():
    assert {f.name for f in dataclasses.fields(RagRequest)} == {
        "question", "question_type", "user_role", "user_id", "as_of_date", "golden_customer_id"}
    assert {f.name for f in dataclasses.fields(RagResponse)} == {
        "answer", "evidence", "citations", "confidence", "not_found", "coverage"}
    RagRequest("q", "rag_corpus", "analyst", "U1", date(2026, 9, 28))


def test_apply_stubs_flags_an_older_table():
    c = duckdb.connect(":memory:")
    c.execute("CREATE SCHEMA gold; CREATE TABLE gold.nba_overrides (recommendation_id VARCHAR)")
    with pytest.raises(StubMismatch):
        apply_stubs(c)


def test_fixtures_load_and_are_consistent():
    c = duckdb.connect(":memory:")
    load_fixtures(c)
    load_fixtures(c)  # re-runnable
    ids = {r[0] for r in c.execute("SELECT golden_customer_id FROM gold.c360_customer").fetchall()}
    for t in ("decisioning_customer", "promises_enriched", "customer_safeguards"):
        assert {r[0] for r in c.execute(f"SELECT golden_customer_id FROM gold.{t}").fetchall()} <= ids
    card = json.loads(c.execute("SELECT decision_card FROM gold.nba_decisions").fetchone()[0])
    assert set(card) == {f.name for f in dataclasses.fields(DecisionCard)}


def test_fixtures_refuse_a_real_warehouse(tmp_path):
    c = duckdb.connect(str(tmp_path / "trustissues.duckdb"))
    with pytest.raises(RuntimeError):
        load_fixtures(c)
    c2 = duckdb.connect(str(tmp_path / "fixtures.duckdb"))
    load_fixtures(c2)


def test_fixture_buckets_use_release_labels():
    c = duckdb.connect(":memory:")
    load_fixtures(c)
    allowed = {"current", "1-30", "31-60", "61-90", "91-120", "121-150", "151-180", "180+"}
    used = {r[0] for r in c.execute("SELECT worst_bucket FROM gold.c360_customer UNION SELECT bucket FROM gold.c360_account "
        "UNION SELECT bucket_start FROM gold.bucket_transitions UNION SELECT bucket_end FROM gold.bucket_transitions").fetchall()}
    assert used <= allowed | {None}


def test_fixture_decision_card_respects_consent():
    c = duckdb.connect(":memory:")
    load_fixtures(c)
    card = json.loads(c.execute("SELECT decision_card FROM gold.nba_decisions").fetchone()[0])
    consent = c.execute("SELECT consent_email FROM gold.customer_safeguards WHERE golden_customer_id='G-000001'").fetchone()[0]
    assert not any(b["rule"] == "consent" and b["action"] == "email" for b in card["blocked_actions"]) or consent != "yes"
