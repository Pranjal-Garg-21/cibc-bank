"""Development-only semantic views over curated tables. Never called on worker warehouse."""
from __future__ import annotations

from pathlib import Path

from .semantic import bucket_sql


def build_dev_adapters(con, *, development_database: bool = False) -> None:
    """Explicit opt-in and isolated DB required. No ingestion, curation or certification happens here.

    Files must be named *_ask_dev.duckdb; use an in-memory connection for fixture development.
    The caller supplies curated tables and P1's gold.customer_xref.
    """
    if not development_database:
        raise ValueError("Development adapters require explicit opt-in")
    for row in con.execute("PRAGMA database_list").fetchall():
        if row[2] and not Path(row[2]).name.endswith("_ask_dev.duckdb"):
            raise ValueError("Use an in-memory or *_ask_dev.duckdb database, never the worker warehouse")
    # Fail before replacing any views if uniqueness would multiply metric populations.
    for table, keys in {
        "curated.collections_cases": "case_id", "curated.agents": "agent_id",
        "curated.case_assignment_history": "assignment_id",
        "curated.promises_to_pay": "ptp_id", "curated.contact_history": "contact_id",
        "curated.card_accounts": "card_account_id,snapshot_date",
        "curated.loan_accounts": "loan_id,snapshot_date",
        "curated.deposit_accounts": "deposit_account_id,snapshot_date",
        "curated.account_monthly_snapshot": "account_id,month_end_date",
        "gold.customer_xref": "source_system,source_key",
    }.items():
        duplicate = con.execute(f"SELECT count(*) FROM (SELECT {keys} FROM {table} GROUP BY {keys} HAVING count(*)>1)").fetchone()[0]
        if duplicate:
            raise ValueError(f"Ambiguous row grain: {table} ({keys})")
    statements = {
        "cases": f"""SELECT c.case_id,x.golden_customer_id,c.case_open_date,c.primary_product,c.queue,
            c.province_code AS province,c.assigned_agent_id,c.current_dpd,c.current_bucket AS reported_bucket,
            {bucket_sql('c.current_dpd')} AS canonical_bucket,c.outcome,c.outcome_date,c.cure_date,
            c.hardship_flag,c.insolvency_hold_flag,c.deceased_hold_flag,c.assignment_method,c.test_cell
            FROM curated.collections_cases c LEFT JOIN gold.customer_xref x
            ON x.source_system='crm' AND x.source_key=c.coll_customer_ref AND x.status IN ('linked','singleton')""",
        "assignments": """SELECT a.assignment_id,a.case_id,a.agent_id,a.assigned_ts,a.unassigned_ts,a.assignment_method,
            a.assignment_reason,a.certified_for_vulnerable_flag,a.hardship_flag_at_assignment,a.dpd_at_assignment,
            c.case_open_date,c.cure_date,c.insolvency_hold_flag,c.deceased_hold_flag
            FROM curated.case_assignment_history a LEFT JOIN gold.semantic_cases c ON c.case_id=a.case_id""",
        "promises": """SELECT p.ptp_id,p.case_id,c.golden_customer_id,p.account_id,p.product,
            coalesce(a.site,'self_serve') AS agent_site,c.queue,c.test_cell,c.assignment_method,
            p.ptp_created_ts,p.ptp_due_date,p.prior_broken_ptp_count,p.ptp_status,p.status_ts,p.paid_date,p.amount_paid_against
            FROM curated.promises_to_pay p LEFT JOIN gold.semantic_cases c ON c.case_id=p.case_id
            LEFT JOIN curated.agents a ON a.agent_id=p.agent_id""",
        "contacts": """SELECT h.contact_id,h.case_id,c.golden_customer_id,
            coalesce(h.channel_v2,h.channel) AS channel,h.direction,h.contact_ts_local,h.contact_hour_local,
            h.rpc_flag,h.ptp_made_flag,h.contact_cost_cad,h.within_permitted_hours_flag,h.dispute_raised_flag,
            c.province,coalesce(h.agent_site,'self_serve') AS agent_site
            FROM curated.contact_history h LEFT JOIN gold.semantic_cases c ON c.case_id=h.case_id""",
        "accounts": f"""SELECT a.card_account_id AS account_id,x.golden_customer_id,a.snapshot_date,'card' AS product,
            a.product_code,a.account_status,a.dpd,a.dpd_bucket AS reported_bucket,{bucket_sql('a.dpd')} AS canonical_bucket,
            a.current_balance AS balance,a.past_due_amount AS overdue_amount,a.chargeoff_date,a.chargeoff_amount
            FROM curated.card_accounts a LEFT JOIN gold.customer_xref x ON x.source_system='card'
            AND x.source_key=a.src_customer_ref AND x.status IN ('linked','singleton')
            UNION ALL
            SELECT a.loan_id,x.golden_customer_id,a.snapshot_date,a.product_type,a.product_code,
            CASE WHEN a.chargeoff_flag THEN 'charged_off' ELSE 'unknown' END AS account_status,
            a.dpd,a.dpd_bucket,{bucket_sql('a.dpd')},a.total_outstanding,a.past_due_amount,
            a.chargeoff_date,a.chargeoff_amount FROM curated.loan_accounts a LEFT JOIN gold.customer_xref x
            ON x.source_system='loan' AND x.source_key=a.src_customer_ref AND x.status IN ('linked','singleton')""",
        "snapshots": f"""SELECT s.account_id,x.golden_customer_id,s.month_end_date AS snapshot_date,s.product_type AS product,
            s.account_status,s.dpd,s.dpd_bucket AS reported_bucket,{bucket_sql('s.dpd')} AS canonical_bucket
            FROM curated.account_monthly_snapshot s LEFT JOIN gold.customer_xref x ON x.source_system='crm'
            AND x.source_key=s.crm_customer_id AND x.status IN ('linked','singleton')""",
        "payroll": """SELECT x.golden_customer_id,a.snapshot_date,bool_or(a.payroll_deposit_flag) AS payroll_deposit_flag,
            max(a.payroll_delay_days) FILTER (WHERE a.payroll_deposit_flag) AS payroll_delay_days
            FROM curated.deposit_accounts a JOIN gold.customer_xref x ON x.source_system='deposit'
            AND x.source_key=a.src_customer_ref AND x.status IN ('linked','singleton')
            WHERE x.golden_customer_id IS NOT NULL GROUP BY x.golden_customer_id,a.snapshot_date""",
    }
    con.execute("BEGIN TRANSACTION")
    try:
        for name, sql in statements.items():
            con.execute(f"CREATE OR REPLACE VIEW gold.semantic_{name} AS {sql}")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
