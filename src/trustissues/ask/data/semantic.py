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
    # One row per case assignment, with the case's cure date and holds carried on the row so the pilot template needs
    # no join. Certification and hardship are as recorded at assignment time, not the agent's or case's state today.
    "gold.semantic_assignments": {
        "assignment_id": "VARCHAR", "case_id": "VARCHAR", "agent_id": "VARCHAR", "assigned_ts": "TIMESTAMP",
        "unassigned_ts": "TIMESTAMP", "assignment_method": "VARCHAR", "assignment_reason": "VARCHAR",
        "certified_for_vulnerable_flag": "BOOLEAN", "hardship_flag_at_assignment": "BOOLEAN",
        "dpd_at_assignment": "BIGINT", "case_open_date": "DATE", "cure_date": "DATE",
        "insolvency_hold_flag": "BOOLEAN", "deceased_hold_flag": "BOOLEAN",
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


# Read by reviewed metric templates only. Not shown to the SQL fallback model and refused there (sql_fallback.py), so
# its prompt and every cached reply stay as they were.
TEMPLATE_ONLY = frozenset({"gold.semantic_assignments"})

SQL_DIR = Path(__file__).parent / "metric_sql"
