"""Proposed P2 semantic contracts. No real warehouse is opened by this module."""
from __future__ import annotations

from pathlib import Path

SCHEMAS = {
    "gold.semantic_cases": {
        "case_id": "VARCHAR", "golden_customer_id": "VARCHAR", "case_open_date": "DATE",
        "primary_product": "VARCHAR", "queue": "VARCHAR", "province": "VARCHAR",
        "assigned_agent_id": "VARCHAR", "current_dpd": "BIGINT", "reported_bucket": "VARCHAR",
        "canonical_bucket": "VARCHAR", "outcome": "VARCHAR", "outcome_date": "DATE", "cure_date": "DATE",
        "hardship_flag": "BOOLEAN", "insolvency_hold_flag": "BOOLEAN", "deceased_hold_flag": "BOOLEAN",
        "assignment_method": "VARCHAR", "test_cell": "VARCHAR",
    },
    "gold.semantic_promises": {
        "ptp_id": "VARCHAR", "case_id": "VARCHAR", "golden_customer_id": "VARCHAR",
        "account_id": "VARCHAR", "product": "VARCHAR", "agent_site": "VARCHAR", "queue": "VARCHAR",
        "test_cell": "VARCHAR", "assignment_method": "VARCHAR", "ptp_created_ts": "TIMESTAMP",
        "ptp_due_date": "DATE", "prior_broken_ptp_count": "BIGINT", "ptp_status": "VARCHAR",
        "status_ts": "TIMESTAMP", "paid_date": "DATE", "amount_paid_against": "DOUBLE",
    },
    "gold.semantic_contacts": {
        "contact_id": "VARCHAR", "case_id": "VARCHAR", "golden_customer_id": "VARCHAR",
        "channel": "VARCHAR", "direction": "VARCHAR", "contact_ts_local": "TIMESTAMP",
        "contact_hour_local": "BIGINT", "rpc_flag": "BOOLEAN", "ptp_made_flag": "BOOLEAN",
        "contact_cost_cad": "DOUBLE", "within_permitted_hours_flag": "BOOLEAN",
        "dispute_raised_flag": "BOOLEAN", "province": "VARCHAR", "agent_site": "VARCHAR",
    },
    "gold.semantic_accounts": {
        "account_id": "VARCHAR", "golden_customer_id": "VARCHAR", "snapshot_date": "DATE",
        "product": "VARCHAR", "product_code": "VARCHAR", "account_status": "VARCHAR",
        "dpd": "BIGINT", "reported_bucket": "VARCHAR", "canonical_bucket": "VARCHAR",
        "balance": "DOUBLE", "overdue_amount": "DOUBLE", "chargeoff_date": "DATE",
        "chargeoff_amount": "DOUBLE",
    },
    "gold.semantic_snapshots": {
        "account_id": "VARCHAR", "golden_customer_id": "VARCHAR", "snapshot_date": "DATE",
        "product": "VARCHAR", "account_status": "VARCHAR", "dpd": "BIGINT",
        "reported_bucket": "VARCHAR", "canonical_bucket": "VARCHAR",
    },
    "gold.semantic_payroll": {
        "golden_customer_id": "VARCHAR", "snapshot_date": "DATE", "payroll_deposit_flag": "BOOLEAN",
        "payroll_delay_days": "BIGINT",
    },
}


def create_semantic_fixtures(con) -> None:
    """Create EMPTY contract tables only in an in-memory connection, never a worker DB."""
    if any(row[2] for row in con.execute("PRAGMA database_list").fetchall()):
        raise ValueError("semantic fixtures require an in-memory database")
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    for table, columns in SCHEMAS.items():
        fields = ", ".join(f'"{name}" {kind}' for name, kind in columns.items())
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({fields})")


def bucket_sql(column: str) -> str:
    """Internal SQL builder. Callers must supply an engineer-approved identifier."""
    return (f"CASE WHEN {column} IS NULL OR {column}<0 THEN NULL WHEN {column}=0 THEN 'current' "
            f"WHEN {column}<=30 THEN '1-30' WHEN {column}<=60 THEN '31-60' "
            f"WHEN {column}<=90 THEN '61-90' WHEN {column}<=120 THEN '91-120' "
            f"WHEN {column}<=150 THEN '121-150' WHEN {column}<=180 THEN '151-180' ELSE '180+' END")


SQL_DIR = Path(__file__).parent / "metric_sql"
