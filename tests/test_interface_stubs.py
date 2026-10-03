"""The stubs load, have the agreed columns, and the decisioning view stays clean (contract rule 4)."""
import duckdb
import pytest

from trustissues.common.interfaces import apply_stubs

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
