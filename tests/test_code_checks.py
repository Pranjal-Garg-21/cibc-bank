"""Code columns against the reference lists: unknown and inactive codes are counted, listed codes are not."""
import duckdb

from trustissues.factory.quality.dq_report import _counts, expand_code_checks


def test_unknown_and_inactive_codes_are_counted():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA curated")
    con.execute("CREATE TABLE curated.code_lookups (code_type VARCHAR, code VARCHAR, active_flag BOOLEAN, effective_from DATE)")
    con.execute("INSERT INTO curated.code_lookups VALUES ('routing_rule', 'RR-01', true, DATE '2025-01-01'), "
                "('routing_rule', 'RR-OLD', false, DATE '2020-01-01')")
    con.execute("CREATE TABLE curated.assignments (routing_rule_id VARCHAR, assigned_ts TIMESTAMP)")
    con.execute("INSERT INTO curated.assignments VALUES ('RR-01', '2026-01-01'), ('RR-03', '2026-01-01'), "
                "('RR-OLD', '2026-01-01'), (NULL, '2026-01-01'), ('RR-01', '2024-06-01')")
    cfg = {"code_checks": [{"id": "V1", "table": "curated.assignments", "column": "routing_rule_id",
                            "code_type": "routing_rule", "date": "assigned_ts"}]}
    checks = {c["id"]: c for c in expand_code_checks(cfg)}
    assert _counts(con, checks["V1"]) == (2, 4)                 # RR-03 unknown, RR-OLD inactive; NULL not counted
    assert con.execute(checks["V1"]["evidence"]).fetchone()[0] in ("RR-03 1, RR-OLD 1", "RR-OLD 1, RR-03 1")
    assert _counts(con, checks["V0"]) == (1, 4)                 # RR-01 used in 2024, before its 2025 effective date
