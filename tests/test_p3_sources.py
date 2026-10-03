"""features/sources.py: explicit allow-list, typed output, and no protected / PII / proxy column (P3 plan step 0)."""
import re

import duckdb
import pytest
import yaml

from trustissues.common import config
from trustissues.features import sources


@pytest.fixture(scope="module")
def sensitivity():
    path = config.CONFIG_DIR / "sensitivity.yaml"
    assert path.exists(), "config/sensitivity.yaml missing: run python -m trustissues.factory.ingest.dictionary"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _where_columns(where: str | None) -> list[str]:
    return re.findall(r"CAST\((\w+) AS", where or "") + [
        c for c in re.findall(r"(\w+)\s*(?:=|LIKE)", where or "") if c != "VARCHAR"]


def test_every_source_column_is_labelled_none(sensitivity):
    """Default-deny: only columns explicitly labelled `none` may be read (not protected, PII, quasi, unclassified)."""
    proxies = set(sensitivity["proxy_columns"])
    bad = []
    for name, s in sources.SOURCES.items():
        labels = sensitivity["tables"][s.table]
        for src_col, _ in s.columns.values():
            label = labels.get(src_col, "unclassified")
            if label != "none" or f"{s.table}.{src_col}" in proxies:
                bad.append(f"{name}: {s.table}.{src_col} is {label}")
    assert not bad, bad


def test_no_protected_or_pii_column_is_selected(sensitivity):
    protected = set(sensitivity["protected_columns"]) | set(sensitivity["proxy_columns"])
    for s in sources.SOURCES.values():
        for src_col, _ in s.columns.values():
            assert f"{s.table}.{src_col}" not in protected
            assert sensitivity["tables"][s.table].get(src_col) not in ("protected", "direct_pii")


def test_customers_is_read_only_for_rules():
    for name, s in sources.SOURCES.items():
        if s.table == "customers":
            assert s.use == "rules", f"{name} reads customers for {s.use}"


def test_feature_sources_carry_no_outcome_columns_except_the_label():
    """Feature sources may carry outcome columns only for the label, masked by as_of until they happen.
    Outcomes for action valuation live in a separate non-feature source."""
    for name, s in sources.SOURCES.items():
        outcome = set(s.columns) & sources.LEAKAGE_COLUMNS
        if s.use == "features":
            assert outcome <= set(s.known_at), f"{name}: outcome columns {outcome - set(s.known_at)} not masked"


@pytest.fixture
def empty_raw():
    """An in-memory raw schema with the source tables as all-VARCHAR, like load_raw makes them."""
    con = duckdb.connect()
    con.execute(f"CREATE SCHEMA {sources.SCHEMA}")
    tables: dict[str, set] = {}
    for s in sources.SOURCES.values():
        tables.setdefault(s.table, set()).update({c for c, _ in s.columns.values()}, _where_columns(s.where))
    for table, cols in tables.items():
        ddl = ", ".join(f'"{c}" VARCHAR' for c in sorted(cols))
        con.execute(f'CREATE TABLE {sources.SCHEMA}."{table}" ({ddl})')
    return con


def test_each_function_returns_a_typed_relation(empty_raw):
    for name in sources.SOURCES:
        rel = getattr(sources, name)(empty_raw)
        assert isinstance(rel, duckdb.DuckDBPyRelation)
        spec = sources.SOURCES[name]
        assert rel.columns == list(spec.columns), name
        for col, type_ in zip(rel.columns, rel.types):
            declared = spec.columns[col][1]
            expected = "VARCHAR[]" if declared == "JSON_LIST" else declared
            assert str(type_) == expected, f"{name}.{col}: {type_} != {expected}"


def test_no_select_star():
    for name in sources.SOURCES:
        assert "*" not in sources.sql(name) and "*" not in sources.sql(name, as_of="2026-01-01")


def test_as_of_hides_outcomes_not_yet_known(empty_raw):
    empty_raw.execute(f"""INSERT INTO {sources.SCHEMA}.promises_to_pay (ptp_id, ptp_created_ts, status_ts, ptp_status)
        VALUES ('A', '2026-01-01 10:00:00', '2026-01-20 12:00:00', 'broken'),
               ('B', '2026-01-01 10:00:00', '2026-01-05 12:00:00', 'kept'),
               ('C', '2026-02-01 10:00:00', '2026-02-02 12:00:00', 'kept')""")
    rows = dict(sources.promises(empty_raw, as_of="2026-01-10 00:00:00").select("ptp_id, ptp_status").fetchall())
    assert rows == {"A": "open", "B": "kept"}  # A not resolved yet, C not created yet


def test_feature_sql_reads_only_feature_sources():
    """Feature SQL may read the spine and feature sources, never rules / evaluation / action sources."""
    from trustissues.features import compute

    feature_views = {f"src_{n}" for n, s in sources.SOURCES.items() if s.use == "features"}
    for g in compute.GROUPS:
        used = set(re.findall(r"\bsrc_\w+", compute.group_sql(g)))
        assert used <= feature_views, f"{g}.sql reads {used - feature_views}"
        assert not re.search(r"\b(raw|curated|gold)\.", compute.group_sql(g)), f"{g}.sql names a table directly"
