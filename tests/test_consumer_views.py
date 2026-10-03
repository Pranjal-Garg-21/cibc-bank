"""Governance guarantees of the consumer layer: the decisioning allow-list and the publish gate.

Text masking is tested with the scrubber in tests/test_text_controls.py."""
import duckdb
import pytest

from trustissues.factory.gold import publish
from trustissues.factory.gold.views import DECISIONING_COLUMNS
from trustissues.factory.quality import contract_checks


def forbidden():
    rule = next(r for r in contract_checks.load_contract()["rules"] if r.get("kind") == "decisioning_allow_list")
    return contract_checks.forbidden_columns(rule["forbidden_extra"])


def test_decisioning_allow_list_has_no_protected_pii_or_proxy_column():
    assert not set(DECISIONING_COLUMNS) & forbidden()


def test_forbidden_list_covers_the_protected_attributes_and_proxies():
    f = forbidden()
    for col in ["gender_code", "marital_status", "citizenship_status", "accessibility_needs_flag", "vulnerability_type",
                "ccb_deposit_flag", "customer_accent_region", "interpreter_used_flag", "first_name", "date_of_birth",
                "national_id_hash", "geo_lat", "age", "fsa"]:
        assert col in f, col


def stage_db(decisioning_cols):
    con = duckdb.connect(":memory:")
    for s in ("gold_stage", "gold", "identity", "curated"):
        con.execute(f"CREATE SCHEMA {s}")
    con.execute(f"CREATE TABLE gold_stage.decisioning_customer ({', '.join(c + ' VARCHAR' for c in decisioning_cols)})")
    con.execute("CREATE TABLE gold_stage.c360_customer (golden_customer_id VARCHAR)")
    return con


def test_contract_flags_a_protected_column_in_decisioning():
    con = stage_db(["golden_customer_id", "segment", "gender_code"])
    q4 = next(r for r in contract_checks.run(con, "gold_stage") if r.id == "Q4")
    assert not q4.passed and q4.blocking and "gender_code" in q4.detail


def test_publish_is_blocked_and_gold_untouched_when_a_rule_fails():
    con = stage_db(["golden_customer_id", "gender_code"])  # also missing most tables: unrunnable rules fail too
    with pytest.raises(publish.PublishBlocked):
        publish.publish(con)
    published = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'gold' "
                            "AND NOT starts_with(table_name, '_')").fetchone()[0]
    assert published == 0
    assert con.execute("SELECT published FROM gold._publish_log").fetchone()[0] is False
