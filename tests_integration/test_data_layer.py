"""Independent SQL audit of the 4 Oct published data, without building any layer.

Definitions corrected after review: semantic_accounts covers loans and cards;
active registry IDs are those used by current identity clusters; source rows are
identity nodes (one source-customer key, not one account); open cases here means
unresolved cases with NULL outcome, including holds, legal and agency work.
"""
import math
import re

import pytest

from trustissues.governance.masking import classify


SEMANTIC = ("cases", "contacts", "promises", "accounts", "snapshots", "payroll")
PUBLISHED = {
    "c360_customer", "c360_account", "customer_xref", "case_customer", "promises_enriched",
    "customer_timeline", "customer_safeguards", "decisioning_customer", "bucket_transitions",
    "identity_review_queue", "customer_relationship", "nba_decisions", "text_features",
    "_publish_log", "_contract_results", *("semantic_" + t for t in SEMANTIC),
}


def test_schema_inventory(warehouse):
    actual = {r[0] for r in warehouse.execute("SELECT schema_name FROM information_schema.schemata").fetchall()}
    required = {"curated", "gold", "identity", "raw"}
    assert required <= actual, f"schema_count={len(actual)}, missing={sorted(required-actual)}"


def test_published_table_inventory(warehouse):
    actual = {r[0] for r in warehouse.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='gold'").fetchall()}
    assert PUBLISHED <= actual, f"gold_table_count={len(actual)}, required_count={len(PUBLISHED)}, missing={sorted(PUBLISHED-actual)}"


def test_latest_publish_succeeded(warehouse):
    rows = warehouse.execute("SELECT attempt_ts,published,failed_rules FROM gold._publish_log ORDER BY attempt_ts DESC LIMIT 1").fetchall()
    assert len(rows) == 1 and rows[0][1] is True, f"latest_publish_row_count={len(rows)}, latest={rows}"


def test_every_blocking_contract_passed(warehouse):
    rows = warehouse.execute("SELECT rule_id,violations,total,passed FROM gold._contract_results WHERE blocking IS TRUE ORDER BY rule_id").fetchall()
    failed = [r for r in rows if r[3] is not True]
    assert rows and not failed, f"blocking_rule_count={len(rows)}, failed_count={len(failed)}, failed(rule,violations,total,passed)={failed}"


@pytest.mark.parametrize("sql", [
    "SELECT (SELECT count(*) FROM curated.collections_cases), (SELECT count(*) FROM gold.semantic_cases), (SELECT count(*) FROM gold.case_customer)",
    "SELECT (SELECT count(*) FROM curated.contact_history), (SELECT count(*) FROM gold.semantic_contacts)",
    "SELECT (SELECT count(*) FROM curated.promises_to_pay), (SELECT count(*) FROM gold.semantic_promises), (SELECT count(*) FROM gold.promises_enriched)",
], ids=["cases", "contacts", "promises"])
def test_row_count_reconciliation(warehouse, sql):
    counts = warehouse.execute(sql).fetchone()
    assert len(set(counts)) == 1, f"row_counts={counts}; independent_SQL={sql}"


def test_semantic_accounts_match_current_credit_account_sources(warehouse):
    counts = warehouse.execute("""
        SELECT (SELECT count(*) FROM gold.semantic_accounts),
               (SELECT count(*) FROM curated.loan_accounts_current),
               (SELECT count(*) FROM curated.card_accounts_current),
               (SELECT count(*) FROM curated.deposit_accounts_current),
               (SELECT count(*) FROM (
                 SELECT loan_id FROM curated.loan_accounts_current UNION ALL
                 SELECT card_account_id FROM curated.card_accounts_current))
    """).fetchone()
    semantic, loans, cards, deposits, union = counts
    assert semantic == union, (
        f"semantic_accounts={semantic}, current_loans={loans}, current_cards={cards}, current_deposits={deposits}, "
        f"credit_union_all={union}, difference={union-semantic}. Deposits are excluded by the credit-only contract.")


def test_snapshot_row_count_reconciliation(warehouse):
    curated, semantic, null_dates = warehouse.execute("""
        SELECT (SELECT count(*) FROM curated.account_monthly_snapshot),
               (SELECT count(*) FROM gold.semantic_snapshots),
               (SELECT count(*) FROM curated.account_monthly_snapshot WHERE month_end_date IS NULL)
    """).fetchone()
    assert curated == semantic, (
        f"curated_snapshots={curated}, semantic_snapshots={semantic}, difference={curated-semantic}, "
        f"curated_null_dates={null_dates}. Known date-format issue: month-first snapshot dates must be kept; "
        "the older benchmark reference drops some of them. This compares all curated rows without that exclusion.")


@pytest.mark.parametrize("table,keys", [
    ("gold.semantic_cases", ("case_id",)), ("gold.semantic_contacts", ("contact_id",)),
    ("gold.semantic_promises", ("ptp_id",)), ("gold.semantic_accounts", ("account_id", "snapshot_date")),
    ("gold.semantic_snapshots", ("account_id", "snapshot_date")),
    ("gold.semantic_payroll", ("golden_customer_id", "snapshot_date")),
    ("gold.c360_customer", ("golden_customer_id",)),
    ("gold.customer_xref", ("source_system", "source_key")),
], ids=["case", "contact", "promise", "account", "snapshot", "payroll", "person", "source-key"])
def test_keys_are_unique_and_non_null(warehouse, table, keys):
    group = ",".join(keys)
    null = " OR ".join(k + " IS NULL" for k in keys)
    total, nulls, duplicate_keys, excess = warehouse.execute(f"""
        WITH duplicates AS (SELECT {group},count(*) AS n FROM {table} GROUP BY {group} HAVING count(*)>1)
        SELECT (SELECT count(*) FROM {table}), (SELECT count(*) FROM {table} WHERE {null}),
               (SELECT count(*) FROM duplicates), (SELECT coalesce(sum(n-1),0) FROM duplicates)
    """).fetchone()
    assert nulls == duplicate_keys == 0, (
        f"table={table}, keys={keys}, rows={total}, null_key_rows={nulls}, duplicate_keys={duplicate_keys}, excess_rows={excess}")


@pytest.mark.parametrize("table", [*("semantic_" + t for t in SEMANTIC), "nba_decisions", "text_features"])
def test_non_null_person_ids_resolve(warehouse, table):
    total, nulls, orphans = warehouse.execute(f"""
        SELECT count(*),count(*) FILTER (WHERE s.golden_customer_id IS NULL),
               count(*) FILTER (WHERE s.golden_customer_id IS NOT NULL AND c.golden_customer_id IS NULL)
        FROM gold.{table} s LEFT JOIN gold.c360_customer c USING (golden_customer_id)
    """).fetchone()
    assert orphans == 0, f"table=gold.{table}, rows={total}, null_people={nulls}, non_null_orphan_people={orphans}"


@pytest.mark.parametrize("table", ["semantic_contacts", "semantic_promises"])
def test_case_orphans_reported_and_below_one_percent(warehouse, table, record_property):
    total, missing, null_case = warehouse.execute(f"""
        SELECT count(*),count(*) FILTER (WHERE c.case_id IS NULL),count(*) FILTER (WHERE s.case_id IS NULL)
        FROM gold.{table} s LEFT JOIN gold.semantic_cases c USING (case_id)
    """).fetchone()
    share = missing / total if total else 0
    summary = f"{table}: rows={total}, orphan_cases={missing}, null_case_ids={null_case}, orphan_pct={share:.6%}, allowed=1%"
    print(summary)
    record_property("orphan_counts", summary)
    assert share <= .01, summary


def test_xref_people_match_c360_and_active_registry(warehouse):
    xref_rows, nulls, people, c360, active_people, registry_rows, active_registry_rows, missing = warehouse.execute("""
        WITH active AS (SELECT DISTINCT golden_customer_id FROM identity.cluster_ids)
        SELECT (SELECT count(*) FROM gold.customer_xref),
               (SELECT count(*) FROM gold.customer_xref WHERE golden_customer_id IS NULL),
               (SELECT count(DISTINCT golden_customer_id) FROM gold.customer_xref),
               (SELECT count(*) FROM gold.c360_customer),
               (SELECT count(*) FROM active),
               (SELECT count(*) FROM identity.golden_id_registry),
               (SELECT count(*) FROM identity.golden_id_registry r
                WHERE EXISTS (SELECT 1 FROM active a WHERE a.golden_customer_id=r.golden_customer_id)),
               (SELECT count(*) FROM active a WHERE NOT EXISTS (
                  SELECT 1 FROM identity.golden_id_registry r WHERE r.golden_customer_id=a.golden_customer_id))
    """).fetchone()
    assert nulls == missing == 0 and people == c360 == active_people == active_registry_rows, (
        f"xref_rows={xref_rows}, null_golden_ids={nulls}, distinct_active_people={people}, c360_rows={c360}, "
        f"active_cluster_people={active_people}, registry_total={registry_rows}, active_registry_rows={active_registry_rows}, "
        f"retired_registry_rows={registry_rows-active_registry_rows}, active_people_missing_from_registry={missing}")


def test_identity_source_customer_nodes_match_xref(warehouse):
    counts = warehouse.execute("""
        SELECT (SELECT count(*) FROM gold.customer_xref), (SELECT count(*) FROM identity.nodes),
               (SELECT count(*) FROM (
                  SELECT source_system,source_key FROM identity.nodes EXCEPT
                  SELECT source_system,source_key FROM gold.customer_xref)),
               (SELECT count(*) FROM (
                  SELECT source_system,source_key FROM gold.customer_xref EXCEPT
                  SELECT source_system,source_key FROM identity.nodes))
    """).fetchone()
    by_source = warehouse.execute("""
        WITH n AS (SELECT source_system,count(*) AS rows FROM identity.nodes GROUP BY source_system),
             x AS (SELECT source_system,count(*) AS rows FROM gold.customer_xref GROUP BY source_system)
        SELECT coalesce(n.source_system,x.source_system),coalesce(n.rows,0),coalesce(x.rows,0)
        FROM n FULL OUTER JOIN x USING (source_system) ORDER BY 1
    """).fetchall()
    xref, nodes, missing, extra = counts
    assert xref == nodes and missing == extra == 0 and all(n == x for _, n, x in by_source), (
        f"xref_source_customer_rows={xref}, identity_nodes={nodes}, missing_keys={missing}, extra_keys={extra}, "
        f"per_source(source,nodes,xref)={by_source}; account row counts are not source-customer counts")


def test_final_2026_10_04_people_count_1011960_update_when_build_changes(warehouse):
    people = warehouse.execute("SELECT count(*) FROM gold.c360_customer").fetchone()[0]
    assert people == 1_011_960, f"final_4_Oct_people={people}, expected=1011960; update this build-pinned test explicitly for a later build"


def test_outbound_call_count_from_curated(warehouse):
    curated, semantic = warehouse.execute("""
        SELECT (SELECT count(*) FROM curated.contact_history WHERE channel_resolved='call' AND direction='outbound'),
               (SELECT count(*) FROM gold.semantic_contacts WHERE channel='call' AND direction='outbound')
    """).fetchone()
    assert curated == semantic, f"curated_outbound_calls={curated}, semantic_outbound_calls={semantic}, difference={curated-semantic}"


def test_amount_paid_against_promises_from_curated(warehouse):
    curated, semantic = warehouse.execute("""
        SELECT (SELECT coalesce(sum(amount_paid_against),0) FROM curated.promises_to_pay),
               (SELECT coalesce(sum(amount_paid_against),0) FROM gold.semantic_promises)
    """).fetchone()
    assert math.isclose(curated, semantic, rel_tol=1e-10, abs_tol=.01), (
        f"curated_paid_CAD={curated:.6f}, semantic_paid_CAD={semantic:.6f}, difference_CAD={curated-semantic:.6f}")


def test_open_cases_from_curated_null_outcome_including_holds(warehouse):
    outcome, status, semantic = warehouse.execute("""
        SELECT (SELECT count(*) FROM curated.collections_cases WHERE outcome IS NULL),
               (SELECT count(*) FROM curated.collections_cases WHERE case_status='open'),
               (SELECT count(*) FROM gold.semantic_cases WHERE outcome IS NULL)
    """).fetchone()
    assert outcome == semantic, (
        f"curated_null_outcome={outcome}, curated_open_status={status}, semantic_null_outcome={semantic}; "
        "definition: unresolved means NULL outcome and includes hold/legal/agency statuses; "
        "the narrower case_status='open' count is diagnostic only")


def test_charged_off_accounts_from_curated(warehouse):
    loans, cards, semantic = warehouse.execute("""
        SELECT (SELECT count(*) FROM curated.loan_accounts_current WHERE chargeoff_date IS NOT NULL),
               (SELECT count(*) FROM curated.card_accounts_current WHERE chargeoff_date IS NOT NULL),
               (SELECT count(*) FROM gold.semantic_accounts WHERE chargeoff_date IS NOT NULL)
    """).fetchone()
    assert loans + cards == semantic, f"curated_charged_off_loans={loans}, cards={cards}, sum={loans+cards}, semantic_charged_off={semantic}"


@pytest.mark.parametrize("table,column", [
    ("semantic_contacts", "contact_ts_local"), ("semantic_promises", "ptp_created_ts"),
    ("semantic_cases", "case_open_date"), ("semantic_snapshots", "snapshot_date"),
    ("semantic_accounts", "snapshot_date"), ("semantic_payroll", "snapshot_date"),
])
def test_semantic_observations_not_after_as_of(warehouse, table, column):
    rows, future, nulls, latest = warehouse.execute(f"""
        SELECT count(*),count(*) FILTER (WHERE {column}>=DATE '2026-09-29'),
               count(*) FILTER (WHERE {column} IS NULL),max({column}) FROM gold.{table}
    """).fetchone()
    assert future == 0, f"table={table}, date_column={column}, rows={rows}, future_rows={future}, null_dates={nulls}, latest={latest}, as_of=2026-09-28"


@pytest.mark.parametrize("table", ["semantic_" + t for t in SEMANTIC])
def test_semantic_columns_are_not_pii_protected_or_free_text(warehouse, table):
    cols = [r[0] for r in warehouse.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='gold' AND table_name=?", [table]).fetchall()]
    bad_levels = [(c, classify(c, table="gold." + table)) for c in cols if classify(c, table="gold." + table) in {"direct_pii", "protected"}]
    prohibited = re.compile(r"(?:^|_)(?:name|phone|email|e_mail|address|postal|dob|date_of_birth|text|notes?|transcript|contact_details)(?:_|$)", re.I)
    free_text = [c for c in cols if prohibited.search(c)]
    assert cols and not bad_levels and not free_text, (
        f"table={table}, column_count={len(cols)}, sensitive_column_count={len(bad_levels)}, sensitive={bad_levels}, free_text_count={len(free_text)}, free_text={free_text}")
