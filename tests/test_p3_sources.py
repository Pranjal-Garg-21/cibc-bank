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


def test_every_source_column_is_labelled_none(sensitivity):
    """Default-deny: only columns explicitly labelled `none` may be read (not protected, PII, quasi, unclassified).
    The one exception is the fairness source, checked by the tests below."""
    proxies = set(sensitivity["proxy_columns"])
    bad = []
    for name, s in sources.SOURCES.items():
        if s.use == "fairness":
            continue
        for src_col, _ in s.columns.values():
            ref = s.label_ref(src_col)
            table, col = ref.split(".")
            label = sensitivity["tables"].get(table, {}).get(col, "unclassified")
            if label != "none" or ref in proxies:
                bad.append(f"{name}: {src_col} ({ref}) is {label}")
    assert not bad, bad


def test_gold_columns_have_lineage(sensitivity):
    """Gold tables are not in sensitivity.yaml: every column read from one must name the source column it comes from
    (label_table or label_as), never fall back to a table the labels do not cover."""
    for name, s in sources.SOURCES.items():
        for src_col, _ in s.columns.values():
            table = s.label_ref(src_col).split(".")[0]
            assert table in sensitivity["tables"], f"{name}: {src_col} has no lineage to a labelled table ({table})"


def test_no_protected_or_pii_column_is_selected(sensitivity):
    protected = set(sensitivity["protected_columns"]) | set(sensitivity["proxy_columns"])
    for s in sources.SOURCES.values():
        if s.use == "fairness":
            continue
        for src_col, _ in s.columns.values():
            ref = s.label_ref(src_col)
            table, col = ref.split(".")
            assert ref not in protected
            assert sensitivity["tables"][table].get(col) not in ("protected", "direct_pii")


def test_customers_is_read_only_for_rules_and_fairness():
    """No feature reads customer attributes. Keys are the exception: the golden and CRM IDs are labelled like
    customers.crm_customer_id, and a feature may carry them to join on."""
    for name, s in sources.SOURCES.items():
        tables = {s.table, *(j.table for j in s.joins)} | {
            s.label_ref(c).split(".")[0] for c, _ in s.columns.values() if s.label_ref(c) != sources.GOLDEN_ID_LABEL}
        if tables & {"customers", "customers_current", "customer_safeguards"}:
            assert s.use in ("rules", "fairness"), f"{name} reads customer attributes for {s.use}"


def test_protected_attributes_are_read_only_by_the_fairness_check(sensitivity):
    """The fairness source is the only one allowed protected columns: it must hold no direct PII, and no module
    but nba/fairness.py may read it (not the features, the model, the rules or the queue)."""
    fair = {n for n, s in sources.SOURCES.items() if s.use == "fairness"}
    assert fair == {"protected_attributes"}
    labels = sensitivity["tables"]["customers"]
    assert all(labels.get(c) != "direct_pii" for c, _ in sources.SOURCES["protected_attributes"].columns.values()
               if c != "crm_customer_id")
    src = config.REPO_ROOT / "src" / "trustissues"
    readers = {p.relative_to(src).as_posix() for p in src.rglob("*.py") if "protected_attributes" in p.read_text()}
    assert readers == {"features/sources.py", "nba/fairness.py"}, readers


def test_feature_sources_carry_no_outcome_columns_except_the_label():
    """Feature sources may carry outcome columns only for the label, masked by as_of until they happen.
    Outcomes for action valuation live in a separate non-feature source."""
    for name, s in sources.SOURCES.items():
        outcome = set(s.columns) & sources.LEAKAGE_COLUMNS
        if s.use == "features":
            assert outcome <= set(s.known_at), f"{name}: outcome columns {outcome - set(s.known_at)} not masked"


@pytest.fixture
def empty_raw():
    """An in-memory warehouse with every source and joined table as all-VARCHAR, like load_raw makes them."""
    con = duckdb.connect()
    sources.create_empty_tables(con)
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
    s = sources.SOURCES["promises"]
    empty_raw.execute(f"""INSERT INTO {s.schema}.{s.table} (ptp_id, ptp_created_ts, status_ts, ptp_status)
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
