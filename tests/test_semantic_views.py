"""gold_stage.semantic_* (P2's Ask tables) built by P1's views: P2's shapes, P1's identity, and P2's certified SQL runs on them."""
from datetime import date

import duckdb
import pytest

from trustissues.ask.access import query_policy
from trustissues.ask.data.execute import execute
from trustissues.ask.data.metric_templates import render_metric
from trustissues.ask.data.semantic import SCHEMAS
from trustissues.ask.data.validator import validate
from trustissues.ask.types import UserContext
from trustissues.factory.gold.views import semantic_views
from trustissues.factory.quality.contract_checks import semantic_shape


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    for s in ("curated", "gold_stage", "gold"):
        c.execute(f"CREATE SCHEMA {s}")
    c.execute("""CREATE TABLE curated.collections_cases AS SELECT * FROM (VALUES
        ('CS-1', 'card', 'early', 'ON', 'AG-1', 45::BIGINT, '31-60', NULL, NULL::DATE, NULL::DATE, false, false, false,
         'random', 'A', 'CRM-WRONG', DATE '2026-08-01', 120.0, NULL::BIGINT),
        ('CS-2', 'loan', 'late', 'QC', 'AG-2', 200::BIGINT, '180+', 'cured', DATE '2026-09-01', DATE '2026-09-01', true,
         false, false, 'rules', 'B', 'CRM-2', DATE '2026-07-01', 0.0, 62::BIGINT))
        t(case_id, primary_product, queue, province_code, assigned_agent_id, current_dpd, current_bucket, outcome,
          outcome_date, cure_date, hardship_flag, insolvency_hold_flag, deceased_hold_flag, assignment_method, test_cell,
          coll_customer_ref, case_open_date, total_overdue, days_to_cure)""")
    # CS-1 names a CRM ID that belongs to someone else: P1's case_customer gives it to the account holder G-1
    c.execute("""CREATE TABLE gold_stage.case_customer AS SELECT * FROM (VALUES ('CS-1', 'G-1'), ('CS-2', 'G-2'))
        t(case_id, golden_customer_id)""")
    c.execute("""CREATE TABLE gold_stage.customer_xref AS SELECT * FROM (VALUES
        ('crm|CRM-WRONG', 'G-9'), ('crm|CRM-2', 'G-2'), ('crm|CRM-3', 'G-3')) t(node_id, golden_customer_id)""")
    c.execute("""CREATE TABLE curated.promises_to_pay AS SELECT * FROM (VALUES
        ('P-1', 'CS-1', 'CRM-WRONG', 'C-1', 'card', 'AG-1', TIMESTAMP '2026-07-01 10:00', DATE '2026-07-10', 0::BIGINT,
         'kept', TIMESTAMP '2026-07-10 12:00', DATE '2026-07-10', 50.0, 'ivr'),
        ('P-2', NULL, 'CRM-3', 'L-1', 'loan', 'AG-X', TIMESTAMP '2026-07-02 10:00', DATE '2026-07-12', 1::BIGINT,
         'broken', TIMESTAMP '2026-07-13 00:00', NULL::DATE, 0.0, 'call'))
        t(ptp_id, case_id, crm_customer_id, account_id, product, agent_id, ptp_created_ts, ptp_due_date,
          prior_broken_ptp_count, ptp_status, status_ts, paid_date, amount_paid_against, ptp_channel)""")
    c.execute("CREATE TABLE curated.agents AS SELECT * FROM (VALUES ('AG-1', 'Toronto')) t(agent_id, site)")
    c.execute("""CREATE TABLE curated.case_assignment_history AS SELECT * FROM (VALUES
        ('AS-1', 'CS-2', 'AG-2', TIMESTAMP '2026-07-02 09:00', NULL::TIMESTAMP, 'randomised_pilot', 'bucket_move', true,
         true, 31::BIGINT)) t(assignment_id, case_id, agent_id, assigned_ts, unassigned_ts, assignment_method,
         assignment_reason, certified_for_vulnerable_flag, hardship_flag_at_assignment, dpd_at_assignment)""")
    c.execute("""CREATE TABLE curated.contact_history AS SELECT * FROM (VALUES
        ('K-1', 'CS-1', 'CRM-WRONG', 'call', 'outbound', TIMESTAMP '2026-07-01 09:00', 9::BIGINT, true, true, 2.4, true,
         false, NULL::VARCHAR, 'PL-1', true)) t(contact_id, case_id, crm_customer_id, channel_resolved, direction, contact_ts_local,
         contact_hour_local, rpc_flag, ptp_made_flag, contact_cost_cad, within_permitted_hours_flag, dispute_raised_flag,
         agent_site, payment_link_id, link_clicked_flag)""")
    c.execute("""CREATE TABLE gold_stage.c360_account AS SELECT * FROM (VALUES
        ('C-1', 'G-1'), ('L-1', 'G-2'), ('D-1', 'G-2'), ('D-2', 'G-2')) t(account_id, golden_customer_id)""")
    c.execute("""CREATE TABLE curated.card_accounts AS SELECT * FROM (VALUES
        ('C-1', DATE '2026-09-28', 'VISA', 'active', 45::BIGINT, '31-60', 900.0, 120.0, NULL::DATE, NULL::DOUBLE))
        t(card_account_id, snapshot_date, product_code, account_status, dpd, dpd_bucket, current_balance, past_due_amount,
          chargeoff_date, chargeoff_amount)""")
    c.execute("""CREATE TABLE curated.loan_accounts AS SELECT * FROM (VALUES
        ('L-1', DATE '2026-09-28', 'personal_loan', 'PL1', true, 200::BIGINT, '180+', 5000.0, 800.0, DATE '2026-09-01', 4000.0))
        t(loan_id, snapshot_date, product_type, product_code, chargeoff_flag, dpd, dpd_bucket, total_outstanding,
          past_due_amount, chargeoff_date, chargeoff_amount)""")
    c.execute("""CREATE TABLE curated.account_monthly_snapshot AS SELECT * FROM (VALUES
        ('C-1', 'CRM-WRONG', DATE '2026-06-30', 'card', 'active', 0::BIGINT, 'current'),
        ('C-1', 'CRM-WRONG', DATE '2026-09-30', 'card', 'active', 45::BIGINT, '31-60'))
        t(account_id, crm_customer_id, month_end_date, product_type, account_status, dpd, dpd_bucket)""")
    c.execute("""CREATE TABLE curated.deposit_accounts AS SELECT * FROM (VALUES
        ('D-1', DATE '2026-09-28', true, 3::BIGINT), ('D-2', DATE '2026-09-28', true, 10::BIGINT))
        t(deposit_account_id, snapshot_date, payroll_deposit_flag, payroll_delay_days)""")
    return c


def rows(con, sql):
    return con.execute(sql).fetchall()


def test_every_table_has_exactly_p2s_columns_and_types(con):
    assert semantic_views(con) == 2 + 1 + 2 + 1 + 2 + 2 + 1
    assert semantic_shape(con, "gold_stage") == []
    con.execute("ALTER TABLE gold_stage.semantic_cases ADD COLUMN extra VARCHAR")  # the gate catches drift both ways
    con.execute("ALTER TABLE gold_stage.semantic_payroll DROP COLUMN payroll_delay_days")
    assert sorted(semantic_shape(con, "gold_stage")) == [
        "semantic_cases.extra: not in the contract", "semantic_payroll.payroll_delay_days: expected BIGINT, found missing"]


def test_rows_belong_to_p1s_person_not_the_crm_id_on_the_record(con):
    semantic_views(con)
    assert rows(con, "SELECT case_id, golden_customer_id FROM gold_stage.semantic_cases ORDER BY 1") == [
        ("CS-1", "G-1"), ("CS-2", "G-2")]  # CS-1's CRM ID points at G-9
    # a promise on a case belongs to the case's person; with no case, to its own CRM ID
    assert rows(con, "SELECT ptp_id, golden_customer_id, agent_site, queue FROM gold_stage.semantic_promises ORDER BY 1") == [
        ("P-1", "G-1", "Toronto", "early"), ("P-2", "G-3", "self_serve", None)]
    assert rows(con, "SELECT golden_customer_id, province, agent_site, payment_link_sent_flag, link_clicked_flag "
                     "FROM gold_stage.semantic_contacts") == [("G-1", "ON", "self_serve", True, True)]
    assert rows(con, "SELECT case_id, total_overdue, days_to_cure FROM gold_stage.semantic_cases ORDER BY 1") == [
        ("CS-1", 120.0, None), ("CS-2", 0.0, 62)]
    assert rows(con, "SELECT ptp_id, ptp_channel FROM gold_stage.semantic_promises ORDER BY 1") == [("P-1", "ivr"), ("P-2", "call")]
    # accounts and snapshots: the holder in c360_account
    assert rows(con, "SELECT DISTINCT golden_customer_id FROM gold_stage.semantic_snapshots") == [("G-1",)]
    assert rows(con, "SELECT account_id, golden_customer_id, account_status, canonical_bucket FROM gold_stage.semantic_accounts ORDER BY 1") == [
        ("C-1", "G-1", "active", "31-60"), ("L-1", "G-2", "charged_off", "180+")]
    assert rows(con, "SELECT * FROM gold_stage.semantic_payroll") == [("G-2", date(2026, 9, 28), True, 10)]
    # an assignment carries its case's cure date and holds, so the pilot template needs no join
    assert rows(con, "SELECT assignment_id, cure_date, insolvency_hold_flag FROM gold_stage.semantic_assignments") == [
        ("AS-1", date(2026, 9, 1), False)]


def test_p2s_certified_metric_runs_on_the_built_tables(con):
    semantic_views(con)
    for t in SCHEMAS:
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM gold_stage.{t.split('.')[1]}")  # what publish does
    q = render_metric("average_dpd", parameters={})
    res = execute(validate(q.sql, query_policy(UserContext("lead", "team_lead"))), q.parameters, con=con)
    assert res.rows[0][0] == 45  # one open case (CS-2 is cured), 45 days past due
