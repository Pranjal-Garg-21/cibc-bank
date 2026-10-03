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


def test_identity_column_typed_as_date_still_standardises(con):
    """Regression: external.subject_dob_reported is a DATE in schema.json; the build crashed on it
    ('No function matches std_dob(DATE)') because the standardise functions only accept text."""
    schema = {"bureau": {"columns": [
        {"name": "ref", "type": "string"}, {"name": "subject_name_reported", "type": "string"},
        {"name": "subject_dob_reported", "type": "date"}, {"name": "subject_address_reported", "type": "string"}]}}
    cfg = {"identity_fields": {"bureau": {
        "name": {"col": "subject_name_reported", "order": "auto", "caps_default": "last_first"},
        "dob": "subject_dob_reported", "postal_from_address": "subject_address_reported"}}}
    con.execute("CREATE TABLE raw.bureau (ref VARCHAR, subject_name_reported VARCHAR, subject_dob_reported VARCHAR, "
                "subject_address_reported VARCHAR, _ingested_at TIMESTAMP, _source_file VARCHAR, _batch_id VARCHAR, _row_hash VARCHAR)")
    con.execute("INSERT INTO raw.bureau VALUES ('0027621481', 'NOWAK, OKSANA ZOFIA', '1996-01-17', "
                "'8625 KINGSWAY RD, SURREY BC V3X0K6', now(), 'f', 'B1', 'h1')")
    B.curate_table(con, "bureau", schema, cfg)
    r = con.execute("SELECT std_dob, std_dob_ambiguous, std_name_last, std_postal, std_fsa FROM curated.bureau").fetchone()
    assert r == ("1996-01-17", False, "NOWAK", "V3X0K6", "V3X")


def test_non_iso_dates_recovered_only_when_unambiguous(con):
    """account_monthly_snapshot.month_end_date arrives as ISO, YYYYMMDD, DD/MM/YYYY and MM/DD/YYYY."""
    schema = {"snap": {"columns": [{"name": "id", "type": "string"}, {"name": "d", "type": "date"}]}}
    con.execute("CREATE TABLE raw.snap (id VARCHAR, d VARCHAR, _ingested_at TIMESTAMP, _source_file VARCHAR, "
                "_batch_id VARCHAR, _row_hash VARCHAR)")
    cases = [("a", "2024-09-30", "2024-09-30"),   # ISO
             ("b", "20240930", "2024-09-30"),     # YYYYMMDD
             ("c", "30/09/2024", "2024-09-30"),   # day first (30 can only be a day)
             ("d", "09/30/2024", "2024-09-30"),   # month first (30 can only be a day)
             ("e", "05/06/2024", None),           # both readings valid: must NOT be guessed
             ("f", "31/02/2024", None),           # impossible date
             ("g", "not a date", None)]
    for cid, raw, _ in cases:
        con.execute("INSERT INTO raw.snap VALUES (?,?, now(),'f','B1',?)", [cid, raw, f"h{cid}"])
    r = B.curate_table(con, "snap", schema, {})
    got = {i: (str(d) if d is not None else None) for i, d in con.execute("SELECT id, d FROM curated.snap").fetchall()}
    assert got == {cid: want for cid, _, want in cases}
    # the three formats that were recovered are counted and logged, the unrecoverable ones stay visible
    assert con.execute("SELECT recovered_rows FROM curated._format_recoveries WHERE column_name='d'").fetchone()[0] == 3
    assert con.execute("SELECT failed_rows FROM curated._cast_failures WHERE column_name='d'").fetchone()[0] == 3
    raw_kept = dict(con.execute("SELECT id, d__raw FROM curated.snap WHERE d__raw IS NOT NULL").fetchall())
    assert raw_kept == {"e": "05/06/2024", "f": "31/02/2024", "g": "not a date"}
    assert r["cols_with_recovered_formats"] == 1


def _people_table(con, name, rows, extra_cols=""):
    con.execute(f"CREATE TABLE raw.{name} (id VARCHAR, dob_raw VARCHAR{extra_cols}, _ingested_at TIMESTAMP, "
                "_source_file VARCHAR, _batch_id VARCHAR, _row_hash VARCHAR)")
    for i, r in enumerate(rows):
        marks = ",".join("?" * len(r))
        con.execute(f"INSERT INTO raw.{name} VALUES ({marks}, now(), 'f', 'B1', ?)", [*r, f"h{name}{i}"])


def test_one_sided_column_resolves_ambiguous_dates_and_records_the_evidence(con):
    # a card-like column: day-first, so the first part is sometimes above 12 and the second never is
    rows = [(f"c{i}", f"{13 + i % 15:02d}-0{1 + i % 9}-1990") for i in range(1200)] + [("amb", "07-12-1992")]
    _people_table(con, "cards", rows)
    cfg = {"identity_fields": {"cards": {"dob": "dob_raw"}}}
    B.curate_table(con, "cards", {"cards": {"columns": [{"name": "id", "type": "string"}, {"name": "dob_raw", "type": "string"}]}}, cfg)
    amb = con.execute("SELECT std_dob, std_dob_alt, std_dob_ambiguous, std_dob_was_ambiguous, std_dob_basis FROM curated.cards WHERE id='amb'").fetchone()
    assert amb == ("1992-12-07", None, False, True, "convention")        # day-first, decided by the column
    ev = con.execute("SELECT first_over_12, second_over_12, decision FROM curated._date_conventions WHERE table_name='cards'").fetchone()
    assert ev == (1200, 0, "day_first")                                  # the evidence is on the record


def test_mixed_column_is_not_guessed(con):
    rows = [(f"a{i}", f"{13 + i % 15:02d}/0{1 + i % 9}/1990") for i in range(600)] + \
           [(f"b{i}", f"0{1 + i % 9}/{13 + i % 15:02d}/1990") for i in range(500)] + [("amb", "07/12/1992")]
    _people_table(con, "mixed", rows)
    cfg = {"identity_fields": {"mixed": {"dob": "dob_raw"}}}
    B.curate_table(con, "mixed", {"mixed": {"columns": [{"name": "id", "type": "string"}, {"name": "dob_raw", "type": "string"}]}}, cfg)
    amb = con.execute("SELECT std_dob_ambiguous, std_dob_alt IS NOT NULL, std_dob_basis FROM curated.mixed WHERE id='amb'").fetchone()
    assert amb == (True, True, "ambiguous")                              # both readings kept
    assert con.execute("SELECT decision FROM curated._date_conventions WHERE table_name='mixed'").fetchone()[0].startswith("mixed")


def test_source_clean_date_settles_an_ambiguous_raw_date(con):
    rows = [("x", "07/12/1992", "1992-12-07"), ("y", "07/12/1992", "1992-07-12"), ("z", "13/01/1990", "1990-01-13"), ("n", "07/12/1992", None)]
    _people_table(con, "crm", rows, extra_cols=", date_of_birth VARCHAR")
    cfg = {"identity_fields": {"crm": {"dob": "dob_raw", "dob_clean": "date_of_birth"}}}
    schema = {"crm": {"columns": [{"name": "id", "type": "string"}, {"name": "dob_raw", "type": "string"}, {"name": "date_of_birth", "type": "date"}]}}
    B.curate_table(con, "crm", schema, cfg)
    got = {i: r for i, *r in con.execute("SELECT id, std_dob, std_dob_ambiguous, std_dob_was_ambiguous, std_dob_basis FROM curated.crm").fetchall()}
    assert got["x"] == ["1992-12-07", False, True, "source_clean_column"]  # same raw text, different truth: the clean column decides
    assert got["y"] == ["1992-07-12", False, True, "source_clean_column"]
    assert got["z"][0] == "1990-01-13"
    assert got["n"][0] is not None and got["n"][1:] == [True, True, "ambiguous"]  # no clean value: stays ambiguous, both readings kept
