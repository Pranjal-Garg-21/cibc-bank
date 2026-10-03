"""Typed source relations for P3. The ONLY module in features/ and nba/ that names a warehouse table.

Every source reads the schema in config/settings.yaml (p3.source_schema): `curated` now (typed,
deduplicated, non-ISO dates recovered); `raw` also works, since everything is cast here, once (but raw
loses ~10% of account_monthly_snapshot.month_end_date, written in non-ISO formats). When P1 publishes
gold, the specs below point at `gold.promises_enriched` / `gold.decisioning_customer` /
`gold.customer_safeguards` and nothing else in P3 changes.

Rules for every spec:
- explicit column allow-list, never `SELECT *`;
- every source column must be labelled `none` in config/sensitivity.yaml (tests enforce it);
- `customers` is read only by `crm_safety_flags`, which is for nba/rules.py and never a feature.

`as_of` truncates a source to what was known strictly before that timestamp. Used for live
("latest") values and by the point-in-time leakage test.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

import duckdb

from trustissues.common import config

SCHEMA = os.environ.get("P3_SOURCE_SCHEMA", config.settings()["p3"].get("source_schema", "curated"))

# Columns that describe the outcome of a promise. Never features (P3 plan §3).
LEAKAGE_COLUMNS = frozenset({"ptp_status", "status_ts", "amount_paid_against", "paid_date", "broken_reason_agent_coded"})


@dataclass(frozen=True)
class Source:
    table: str
    # output column -> (source column, type). Type is a DuckDB type, or JSON_LIST for a JSON array of strings.
    columns: dict[str, tuple[str, str]]
    use: str  # features | label | rules | evaluation | actions
    event_ts: str | None = None  # output column: when the row became known (for as_of)
    where: str | None = None  # filter on source columns
    # output column -> (output column holding when it became known, value before that)
    known_at: dict[str, tuple[str, str]] = field(default_factory=dict)


def _c(*names: str, type_: str = "VARCHAR") -> dict[str, tuple[str, str]]:
    return {n: (n, type_) for n in names}


SOURCES: dict[str, Source] = {
    "promises": Source(
        table="promises_to_pay",
        use="features",
        event_ts="ptp_created_ts",
        columns={
            **_c("ptp_id", "case_id", "crm_customer_id", "account_id", "product", "ptp_channel", "ptp_type",
                 "payment_method_promised"),
            **_c("ptp_created_ts", "status_ts", type_="TIMESTAMP"),
            **_c("ptp_due_date", "customer_stated_income_date", type_="DATE"),
            **_c("ptp_amount", "overdue_at_creation", "ptp_to_overdue_ratio", type_="DOUBLE"),
            **_c("days_to_due_at_creation", "ptp_sequence_in_case", "prior_broken_ptp_count", "dpd_at_creation",
                 "grace_days", type_="INTEGER"),
            "reminder_scheduled_flag": ("reminder_scheduled_flag", "BOOLEAN"),
            # outcome: for the label and for history features "resolved before the event time" only
            "ptp_status": ("ptp_status", "VARCHAR"),
        },
        known_at={"ptp_status": ("status_ts", "'open'"), "status_ts": ("status_ts", "NULL")},
    ),
    "account_snapshots": Source(
        table="account_monthly_snapshot",
        use="features",
        event_ts="month_end_date",
        columns={
            **_c("account_id", "crm_customer_id", "product_type", "account_status", "dpd_bucket"),
            "month_end_date": ("month_end_date", "DATE"),
            **_c("balance", "limit_or_original_amount", "minimum_due", "payment_made", "salary_credit_amount",
                 type_="DOUBLE"),
            "dpd": ("dpd", "INTEGER"),
            **_c("overlimit_flag", "write_off_flag", type_="BOOLEAN"),
        },
    ),
    "contacts": Source(
        table="contact_history",
        use="features",
        event_ts="contact_ts",
        columns={
            **_c("contact_id", "case_id", "crm_customer_id", "account_id", "channel", "direction", "treatment_code",
                 "test_cell", "bucket_at_contact"),
            # local time: ptp_created_ts is recorded in customer-local time too
            "contact_ts": ("contact_ts_local", "TIMESTAMP"),
            **_c("rpc_flag", "ptp_made_flag", "email_delivered_flag", "email_opened_flag", "email_clicked_flag",
                 "sms_delivered_flag", "link_clicked_flag", "push_delivered_flag", "push_opened_flag",
                 "payment_taken_flag", type_="BOOLEAN"),
            **_c("payment_amount", "contact_cost_cad", type_="DOUBLE"),
            "dpd_at_contact": ("dpd_at_contact", "INTEGER"),
        },
    ),
    "cases": Source(
        table="collections_cases",
        use="rules",
        event_ts="case_open_date",
        columns={
            **_c("case_id", "case_status", "primary_account_id", "primary_product", "test_cell", "strategy_code",
                 "assignment_method", "dispute_status", "current_bucket"),
            "crm_customer_id": ("coll_customer_ref", "VARCHAR"),
            **_c("case_open_date", "outcome_date", type_="DATE"),
            **_c("entry_dpd", "current_dpd", "contact_cap_7d", "contacts_last_7d", type_="INTEGER"),
            **_c("total_overdue", "entry_overdue", "payments_since_open_amount", type_="DOUBLE"),
            **_c("hardship_flag", "dispute_flag", "cease_contact_flag", "third_party_rep_flag", "insolvency_hold_flag",
                 "deceased_hold_flag", "vulnerable_customer_flag", "cure_flag", type_="BOOLEAN"),
        },
    ),
    "hardship_programs": Source(
        table="hardship_programs",
        use="rules",
        columns={
            **_c("program_id", "version", "program_name", "min_hardship_level", "approver_level"),
            "product_types": ("product_types", "JSON_LIST"),
            **_c("max_dpd", "min_months_on_book", "max_uses_12m", "duration_months", type_="INTEGER"),
            **_c("excludes_insolvency", "excludes_broken_plan_90d", "requires_income_evidence", type_="BOOLEAN"),
            "payment_reduction_pct": ("payment_reduction_pct", "DOUBLE"),
            **_c("effective_from", "effective_to", type_="DATE"),
        },
    ),
    "incumbent_ptp_scores": Source(
        table="model_scores",
        use="evaluation",
        event_ts="score_ts",
        where=f"model_name = '{config.settings()['p3']['incumbent_model']}'",
        columns={
            **_c("ptp_id", "model_version"),
            "score_date": ("score_date", "DATE"),
            "score_ts": ("score_ts", "TIMESTAMP"),
            "score": ("score", "DOUBLE"),
        },
    ),
    # CRM safety flags: nba/rules.py ONLY (P3 plan §4 step 6). Never a model feature.
    "crm_safety_flags": Source(
        table="customers",
        use="rules",
        where="CAST(is_deleted AS VARCHAR) = 'false' AND CAST(effective_to_ts AS VARCHAR) LIKE '9999%'",
        columns={
            "crm_customer_id": ("crm_customer_id", "VARCHAR"),
            **_c("consent_call", "consent_sms", "consent_email", "consent_push", "consent_letter",
                 "cease_communication_flag", "deceased_flag", "bankruptcy_flag", "consumer_proposal_flag",
                 "vulnerability_flag", "national_dncl_flag", "internal_do_not_call_flag", "legal_representative_flag",
                 type_="BOOLEAN"),
            **_c("preferred_contact_window_start", "preferred_contact_window_end", "permitted_call_start_local",
                 "permitted_call_end_local"),
        },
    ),
    # Promise outcomes for action valuation (nba/actions.py) only. Never a feature.
    "promise_outcomes": Source(
        table="promises_to_pay",
        use="actions",
        columns={
            **_c("ptp_id", "case_id", "ptp_status"),
            "status_ts": ("status_ts", "TIMESTAMP"),
            "paid_date": ("paid_date", "DATE"),
            "amount_paid_against": ("amount_paid_against", "DOUBLE"),
        },
    ),
    "channel_capacity": Source(
        table="channel_capacity",
        use="actions",
        columns={
            "date": ("date", "DATE"),
            **_c("channel", "time_band"),
            **_c("unit_cost_cad", "capacity_units", "used_units", type_="DOUBLE"),
        },
    ),
}


def _expr(src_col: str, type_: str) -> str:
    col = f'"{src_col}"'
    if type_ == "JSON_LIST":
        return f"CAST(from_json({col}, '[\"VARCHAR\"]') AS VARCHAR[])"
    if type_ == "VARCHAR":
        return col
    return f"TRY_CAST({col} AS {type_})"


def sql(name: str, as_of: datetime | str | None = None) -> str:
    """SELECT for one source. With as_of, only what was known strictly before it."""
    s = SOURCES[name]
    typed = ", ".join(f'{_expr(c, t)} AS "{out}"' for out, (c, t) in s.columns.items())
    inner = f'SELECT {typed} FROM {SCHEMA}."{s.table}"' + (f" WHERE {s.where}" if s.where else "")
    if as_of is None or s.event_ts is None:
        return inner
    ts = f"TIMESTAMP '{as_of}'"
    outs = []
    for out in s.columns:
        if out in s.known_at:
            known_col, before = s.known_at[out]
            outs.append(f'CASE WHEN "{known_col}" < {ts} THEN "{out}" ELSE {before} END AS "{out}"')
        else:
            outs.append(f'"{out}"')
    return f'SELECT {", ".join(outs)} FROM ({inner}) WHERE "{s.event_ts}" < {ts}'


def relation(con: duckdb.DuckDBPyConnection, name: str, as_of=None) -> duckdb.DuckDBPyRelation:
    return con.sql(sql(name, as_of))


def register(con: duckdb.DuckDBPyConnection, as_of=None) -> None:
    """Create temp views src_<name> for every source (what the feature SQL files read)."""
    for name in SOURCES:
        con.execute(f"CREATE OR REPLACE TEMP VIEW src_{name} AS {sql(name, as_of)}")


def promises(con, as_of=None):
    return relation(con, "promises", as_of)


def account_snapshots(con, as_of=None):
    return relation(con, "account_snapshots", as_of)


def contacts(con, as_of=None):
    return relation(con, "contacts", as_of)


def cases(con, as_of=None):
    return relation(con, "cases", as_of)


def hardship_programs(con):
    return relation(con, "hardship_programs")


def incumbent_ptp_scores(con):
    return relation(con, "incumbent_ptp_scores")


def crm_safety_flags(con):
    """Rules only (nba/rules.py). Never a model feature."""
    return relation(con, "crm_safety_flags")


def channel_capacity(con):
    return relation(con, "channel_capacity")


def promise_outcomes(con):
    """Action valuation only (nba/actions.py). Never a feature."""
    return relation(con, "promise_outcomes")
