import re

import duckdb
import pytest

from trustissues.factory.quality import dq_report as D


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("CREATE SCHEMA curated")
    c.execute("CREATE TABLE curated.customers (crm_customer_id VARCHAR)")
    c.execute("INSERT INTO curated.customers VALUES ('AAA-0001'), ('AAA-0002')")
    c.execute("CREATE TABLE curated.contact_history (crm_customer_id VARCHAR, channel VARCHAR, channel_v2 VARCHAR, "
              "contact_ts_utc TIMESTAMP, dq_unused INTEGER)")
    c.execute("INSERT INTO curated.contact_history VALUES "
              "('AAA-0001', 'sms', NULL, '2026-09-01', 0), "          # old style: channel only
              "('AAA-0002', NULL, 'call', '2026-09-27', 0), "         # after the rename: channel_v2 only
              "('ZZZ-9999', NULL, 'email', '2026-09-27', 0), "        # orphan customer
              "('ZZZ-9999', 'ivr', 'ivr', '2026-09-27', 0)")          # both set, same
    c.execute("CREATE TABLE curated.contact_history_current AS SELECT * FROM curated.contact_history")
    c.execute("CREATE TABLE curated.ops_events (event_id VARCHAR, affected_customers_est BIGINT)")
    c.execute("INSERT INTO curated.ops_events VALUES ('REL-0926', 0), ('INC-0923', 35000)")
    return c


CFG = {
    "derived_columns": [{"table": "curated.contact_history", "column": "channel_resolved", "type": "VARCHAR",
                         "expr": "coalesce(channel_v2, channel)", "from": "channel"}],
    "orphan_refs": [{"table": "curated.contact_history", "column": "crm_customer_id",
                     "parent": "curated.customers", "parent_column": "crm_customer_id"}],
    "checks": [
        {"id": "K1", "area": "known_incident", "event": "INC-0923", "title": "rename", "tables": ["contact_history"],
         "severity": "high", "status": "fixed", "how": "derived column",
         "sql": "select count(*) filter (where channel is null and channel_v2 is not null), count(*) from curated.contact_history",
         "evidence": "select 'no channel left: ' || count(*) filter (where channel_resolved is null) from curated.contact_history"},
        {"id": "L1", "area": "logic", "title": "after the rename", "tables": ["contact_history"], "severity": "low",
         "status": "flagged", "how": "flag", "table": "curated.contact_history",
         "where": "contact_ts_utc > date '2026-09-26'", "flag": "dq_after_release"},
        {"id": "L2", "area": "logic", "title": "condition that can be NULL", "tables": ["contact_history"],
         "severity": "low", "status": "flagged", "how": "flag", "table": "curated.contact_history",
         "where": "channel = 'sms'", "flag": "dq_is_sms"},
        {"id": "M1", "area": "missing", "title": "table that does not exist", "tables": ["nope"], "severity": "low",
         "status": "documented", "how": "n/a", "table": "curated.nope", "where": "true"},
    ],
    "clean_checks": [{"id": "C1", "title": "nothing wrong", "table": "curated.customers", "where": "crm_customer_id is null"}],
}


def test_handling_is_applied_and_idempotent(con):
    for _ in range(2):  # twice: must not fail or duplicate
        D.apply_derived(con, CFG)
        D.apply_flags(con, CFG)
    got = con.execute("select channel_resolved from curated.contact_history order by 1").fetchall()
    assert [g[0] for g in got] == ["call", "email", "ivr", "sms"]
    assert con.execute("select count(*) from curated.contact_history_current where channel_resolved is null").fetchone()[0] == 0
    # flag: true/false, never NULL, even where the condition itself is NULL (channel is NULL on two rows)
    assert con.execute("select count(*) filter (where dq_after_release), count(*) filter (where not dq_after_release), "
                       "count(*) filter (where dq_is_sms is null) from curated.contact_history").fetchone() == (3, 1, 0)
    assert con.execute("select count(*) filter (where dq_is_sms) from curated.contact_history_current").fetchone()[0] == 1


def test_orphan_references_are_listed(con):
    assert D.build_orphans(con, CFG) == 1
    assert con.execute("select table_name, ref_id, row_count from curated.dq_orphan_customer_refs").fetchall() == [
        ("contact_history", "ZZZ-9999", 2)]


def test_evaluate_counts_evidence_reported_and_errors(con):
    D.apply_derived(con, CFG)
    D.apply_flags(con, CFG)
    issues, clean = D.evaluate(con, CFG)
    by = {r.id: r for r in issues}
    assert (by["K1"].affected, by["K1"].total) == (2, 4) and by["K1"].reported == 35000
    assert by["K1"].evidence == "no channel left: 0"
    assert by["L1"].affected == 3 and abs(by["L1"].pct - 75.0) < 1e-9
    assert by["M1"].error and by["M1"].affected is None          # a missing table is reported, not fatal
    assert clean[0].affected == 0 and clean[0].error is None


def test_report_is_consistent(con):
    D.apply_derived(con, CFG)
    D.apply_flags(con, CFG)
    issues, clean = D.evaluate(con, CFG)
    text = D.render(issues, clean, CFG, con)
    assert "# Data Quality Report" in text and "K1" in text and "rename" in text
    assert "could not run" in text and "table that does not exist" in text
    # the "(section N)" cross-references must point at a heading that really has that number
    for label, heading in (("found nothing", "Checks that found nothing"), ("could not run", "Checks that could not run")):
        m = re.search(r"\(section (\d+)\)", text[: text.index("How to read the handling column")]) if label == "found nothing" else \
            re.search(r"could not run\*\* \(listed in section (\d+)\)|\(listed in section (\d+)\)", text)
        n = int([g for g in m.groups() if g][0])
        assert re.search(rf"^## {n}\. {heading}", text, re.M), (label, n)
    # a context event is shown but never counted as an issue
    assert "issues found" in text


def test_checks_file_is_valid():
    cfg = D.load_checks()
    ids = [c["id"] for c in cfg["checks"]] + [c["id"] for c in cfg["clean_checks"]]
    assert len(ids) == len(set(ids)), "check ids must be unique"
    areas = {a for a, _ in D.AREAS}
    for c in cfg["checks"]:
        assert c["area"] in areas and c["status"] in D.STATUS_TEXT and c["severity"] in D.SEVERITY_ORDER, c["id"]
        assert c.get("sql") or (c.get("table") and c.get("where")), c["id"]
        assert c["how"].strip(), c["id"]
        if c.get("flag"):
            assert c.get("table") and c.get("where"), c["id"]  # a flag needs a table and a condition
