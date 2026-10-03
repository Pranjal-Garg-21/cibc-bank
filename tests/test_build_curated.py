import duckdb
import pytest

from trustissues.factory.curate import build_curated as B


@pytest.fixture
def con():
    c = duckdb.connect()
    for s in ("raw", "curated"):
        c.execute(f"CREATE SCHEMA {s}")
    c.execute("CREATE TABLE curated._cast_failures (table_name VARCHAR, column_name VARCHAR, target_type VARCHAR, "
              "failed_rows BIGINT, example_raw VARCHAR, recorded_at TIMESTAMP)")
    c.execute("CREATE TABLE curated._build_log (table_name VARCHAR, raw_rows BIGINT, curated_rows BIGINT, "
              "dupes_removed BIGINT, cols_with_cast_failures INTEGER, secs DOUBLE, built_at TIMESTAMP)")
    B.register_udfs(c)
    return c


SCHEMA = {"acct": {"columns": [
    {"name": "id", "type": "string"}, {"name": "bal", "type": "decimal(12,2)"},
    {"name": "opened", "type": "date"}, {"name": "flag", "type": "boolean"}, {"name": "n", "type": "int"},
    {"name": "holder_name_raw", "type": "string"}, {"name": "holder_dob_raw", "type": "string"},
    {"name": "holder_phone_raw", "type": "string"}, {"name": "holder_postal_raw", "type": "string"},
]}}
CFG = {"identity_fields": {"acct": {
    "name": {"col": "holder_name_raw", "order": "auto", "caps_default": "last_first"},
    "dob": "holder_dob_raw", "phones": {"primary": "holder_phone_raw"}, "postal": "holder_postal_raw"}}}


def _raw(con, rows):
    con.execute("CREATE TABLE raw.acct (id VARCHAR, bal VARCHAR, opened VARCHAR, flag VARCHAR, n VARCHAR, "
                "holder_name_raw VARCHAR, holder_dob_raw VARCHAR, holder_phone_raw VARCHAR, holder_postal_raw VARCHAR, "
                "_ingested_at TIMESTAMP, _source_file VARCHAR, _batch_id VARCHAR, _row_hash VARCHAR)")
    for i, r in enumerate(rows):
        con.execute("INSERT INTO raw.acct VALUES (?,?,?,?,?,?,?,?,?, now(), 'f', 'B1', ?)", [*r[:9], f"h{i}" if len(r) == 9 else r[9]])


def test_types_dedupe_failed_casts_and_identity(con):
    good = ("0012", "10.50", "2026-01-02", "true", "7", "PITTMAN S", "19950627", "(604) 533-6260", "m5v 2t6")
    bad = ("0013", "oops", "02/01/2026", "maybe", "x", "Guo Yan", "07/12/1992", "450672xxx81", "bad")
    dup = good + ("h0",)  # exact duplicate row hash of the first
    _raw(con, [good, bad, dup])
    r = B.curate_table(con, "acct", SCHEMA, CFG)
    assert (r["raw"], r["curated"], r["dupes_removed"]) == (3, 2, 1)

    row = con.execute("SELECT * FROM curated.acct WHERE id='0012'").fetchdf().iloc[0]
    assert row["bal"] == 10.5 and str(row["opened"])[:10] == "2026-01-02" and row["flag"] == True and row["n"] == 7  # noqa: E712
    assert row["std_name_last"] == "PITTMAN" and row["std_name_first"] == "S"
    assert row["std_phone_primary_e164"] == "+16045336260"
    assert row["std_postal"] == "M5V2T6" and row["std_dob"] == "1995-06-27"

    bad_row = con.execute("SELECT * FROM curated.acct WHERE id='0013'").fetchdf().iloc[0]
    assert bad_row["bal"] != bad_row["bal"]  # NaN == null
    assert bad_row["bal__raw"] == "oops" and bad_row["opened__raw"] == "02/01/2026"  # original text kept
    assert bad_row["std_dob_ambiguous"] == True and bad_row["std_phone_primary_masked"] == True  # noqa: E712
    assert bad_row["std_name_last"] == "YAN"  # mixed case ignores the ALL-CAPS default, so natural order

    fails = dict(con.execute("SELECT column_name, failed_rows FROM curated._cast_failures").fetchall())
    assert fails == {"bal": 1, "opened": 1, "flag": 1, "n": 1}
    assert con.execute("SELECT id FROM curated.acct WHERE id='0012'").fetchone()[0] == "0012"  # leading zeros kept


def test_unregistered_table_passes_through_as_text(con):
    con.execute("CREATE TABLE raw.newthing (a VARCHAR, b VARCHAR, _ingested_at TIMESTAMP, _source_file VARCHAR, "
                "_batch_id VARCHAR, _row_hash VARCHAR)")
    con.execute("INSERT INTO raw.newthing VALUES ('1','', now(),'f','B1','h')")
    r = B.curate_table(con, "newthing", {}, {})
    assert r["curated"] == 1
    assert con.execute("SELECT a, b FROM curated.newthing").fetchone() == ("1", None)  # blank -> NULL, no type guessed
