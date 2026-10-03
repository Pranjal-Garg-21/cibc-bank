"""Ingest is dynamic: new files load, unchanged files are skipped, drift is caught."""
import json

import duckdb
import pytest

from trustissues.factory.ingest import load_raw, manifest, schema_drift
from trustissues.factory.ingest.dictionary import is_proxy


@pytest.fixture
def con(tmp_path):
    c = duckdb.connect(str(tmp_path / "t.duckdb"))
    c.execute("CREATE SCHEMA raw")
    return c


def _src(tmp_path, name, text, expected):
    p = tmp_path / f"{name}.csv"
    p.write_text(text, encoding="utf-8")
    return load_raw.Source(name, "csv", [manifest.stat(p, tmp_path)], expected), p


def test_load_skip_then_append(con, tmp_path):
    src, p = _src(tmp_path, "t", "id,v\n0012,a\n0013,b\n", ["id", "v"])
    assert load_raw.load_source(con, src, "B1", ())["rows"] == 2
    # leading zeros survive: raw is text exactly as received
    assert con.execute("SELECT min(id) FROM raw.t").fetchone()[0] == "0012"
    assert load_raw.load_source(con, src, "B2", ())["status"] == "unchanged"

    p.write_text("id,v\n0012,a\n0013,b\n0014,c\n", encoding="utf-8")  # new row appended
    src2 = load_raw.Source("t", "csv", [manifest.stat(p, tmp_path)], ["id", "v"])
    assert load_raw.load_source(con, src2, "B3", ())["rows"] == 1
    assert con.execute("SELECT count(*) FROM raw.t").fetchone()[0] == 3


def test_new_unregistered_source_loads(con, tmp_path):
    src, _ = _src(tmp_path, "brand_new", "a,b\n1,2\n", None)
    assert load_raw.load_source(con, src, "B1", ())["rows"] == 1


def test_removed_column_blocks_and_suggests_rename(con, tmp_path):
    src, _ = _src(tmp_path, "t", "id,channel_v2\n1,sms\n", ["id", "channel"])
    with pytest.raises(schema_drift.SchemaDriftError, match="channel -> channel_v2"):
        load_raw.load_source(con, src, "B1", ())


def test_new_column_warns_but_loads(con, tmp_path):
    src, _ = _src(tmp_path, "t", "id,extra\n1,x\n", ["id"])
    r = load_raw.load_source(con, src, "B1", ())
    assert r["unexpected"] == ["extra"] and r["rows"] == 1


def test_proxy_detection():
    assert is_proxy("accent_group") and is_proxy("interpreter_used") and is_proxy("customer_age")
    assert not is_proxy("language") and not is_proxy("days_ageing_bucket") and not is_proxy("account_age_months")
