"""Typed source relations for P3. The ONLY module in features/ and nba/ that names a warehouse table.

The person-level sources read P1's published gold layer: `promises` (gold.promises_enriched), `customer_xref`
(CRM record -> golden customer), `account_owner` (gold.c360_account: account -> golden customer) and
`crm_safety_flags` (gold.customer_safeguards, per CRM record); the two case sources carry the case's golden
customer from gold.case_customer. Everything else reads the schema in config/settings.yaml (p3.source_schema):
`curated` (typed, deduplicated, non-ISO dates recovered). Columns gold does not have yet are joined from curated
(Step 0b of docs/p3_golden_runbook.md).

Rules for every spec:
- explicit column allow-list, never `SELECT *`;
- every source column must be labelled `none` in config/sensitivity.yaml (tests enforce it), except the one
  `use="fairness"` source, `protected_attributes`, which only nba/fairness.py may read (tests enforce that too).
  Gold tables are not labelled, so a gold column names the source column it is built from (`label_table`,
  `label_as`) and is checked against that;
- customer attributes (`customers`, `customer_safeguards`) are read only by `crm_safety_flags` (nba/rules.py) and
  `protected_attributes` (nba/fairness.py), never as a feature.

`as_of` truncates a source to what was known strictly before that timestamp. Used for live
("latest") values and by the point-in-time leakage test.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import duckdb

from trustissues.common import config

SCHEMA = os.environ.get("P3_SOURCE_SCHEMA", config.settings()["p3"].get("source_schema", "curated"))

# Columns that describe the outcome of a promise. Never features (P3 plan §3).
LEAKAGE_COLUMNS = frozenset({"ptp_status", "status_ts", "amount_paid_against", "paid_date", "broken_reason_agent_coded",
                             "cure_date", "case_status", "case_status_date"})
CASE_DAYS = (7, 14, 21, 30, 45, 60, 90)  # collections_cases.dpd_at_day_<k> / overdue_at_day_<k>


@dataclass(frozen=True)
class Join:
    """A LEFT JOIN onto the main table (alias `t`). Columns from it are named `alias.column` in Source.columns."""
    alias: str
    table: str
    on: str
    schema: str = SCHEMA


@dataclass(frozen=True)
class Source:
    table: str
    # output column -> (source column, type). Type is a DuckDB type, or JSON_LIST for a JSON array of strings.
    # A source column is a column of the main table, `alias.column` of one of the joins, or an SQL expression over
    # `alias.column`s (anything with a parenthesis), which must then be listed in label_as.
    columns: dict[str, tuple[str, str]]
    use: str  # features | label | rules | evaluation | actions | fairness
    event_ts: str | None = None  # output column: when the row became known (for as_of)
    where: str | None = None  # filter on source columns
    # output column -> (SQL for when it became known, value before that). The SQL may name output columns.
    known_at: dict[str, tuple[str, str]] = field(default_factory=dict)
    schema: str = SCHEMA  # gold sources set "gold"
    joins: tuple[Join, ...] = ()
    # Lineage for the sensitivity check. config/sensitivity.yaml labels the source tables (curated / raw) only, so a
    # gold column is checked against the column it is built from: source column -> "table.column". Columns not
    # listed use the label of the same name in `label_table` (default: `table`).
    label_table: str | None = None
    label_as: dict[str, str] = field(default_factory=dict)

    def label_ref(self, src_col: str) -> str:
        """The `table.column` whose label in config/sensitivity.yaml applies to a source column."""
        if src_col in self.label_as:
            return self.label_as[src_col]
        if "." in src_col:
            alias, col = src_col.split(".", 1)
            return f"{next(j.table for j in self.joins if j.alias == alias)}.{col}"
        return f"{self.label_table or self.table}.{src_col}"


def _c(*names: str, type_: str = "VARCHAR") -> dict[str, tuple[str, str]]:
    return {n: (n, type_) for n in names}


# The golden customer ID is P1's surrogate key for a person (gold.customer_xref): labelled like the CRM ID it replaces.
GOLDEN_ID_LABEL = "customers.crm_customer_id"
# A case's person: P1 resolves it from the account holders, so cases whose CRM ID does not exist still get one.
CASE_PERSON = Join("cc", "case_customer", "cc.case_id = t.case_id", schema="gold")
CRM_PERSON = Join("x", "customer_xref", "x.node_id = 'crm|' || t.crm_customer_id", schema="gold")
# A collections record belongs to its case's person, else to its CRM record's person (P1's rule, gold/views.py _owner).
RECORD_PERSON = "coalesce(cc.golden_customer_id, x.golden_customer_id)"

SOURCES: dict[str, Source] = {
    # gold.promises_enriched is curated.promises_to_pay with the golden ID added; reminder_scheduled_flag is not in
    # gold yet, so it comes from curated by ptp_id. The label is P3's own, from ptp_status (partially kept = 1); P1's
    # label_broken (partially kept = 0) is never read (p3_plan.md, golden records Step 0).
    "promises": Source(
        table="promises_enriched",
        schema="gold",
        use="features",
        event_ts="ptp_created_ts",
        joins=(Join("r", "promises_to_pay", "r.ptp_id = t.ptp_id"),),
        label_table="promises_to_pay",
        label_as={"golden_customer_id": GOLDEN_ID_LABEL},
        columns={
            **_c("ptp_id", "case_id", "crm_customer_id", "golden_customer_id", "account_id", "product", "ptp_channel",
                 "ptp_type", "payment_method_promised"),
            **_c("ptp_created_ts", "status_ts", type_="TIMESTAMP"),
            **_c("ptp_due_date", "customer_stated_income_date", type_="DATE"),
            **_c("ptp_amount", "overdue_at_creation", "ptp_to_overdue_ratio", type_="DOUBLE"),
            **_c("days_to_due_at_creation", "ptp_sequence_in_case", "prior_broken_ptp_count", "dpd_at_creation",
                 "grace_days", type_="INTEGER"),
            "reminder_scheduled_flag": ("r.reminder_scheduled_flag", "BOOLEAN"),
            # outcome: for the label and for history features "resolved before the event time" only
            "ptp_status": ("ptp_status", "VARCHAR"),
        },
        known_at={"ptp_status": ('"status_ts"', "'open'"), "status_ts": ('"status_ts"', "NULL")},
    ),
    # Source record -> person. CRM records only: the other systems' keys (card numbers, CIFs) are PII.
    "customer_xref": Source(
        table="customer_xref",
        schema="gold",
        use="features",
        where="source_system = 'crm'",
        label_table="customers",
        # status is P1's matching outcome (linked / singleton / in_review ...), metadata about the key
        label_as={"golden_customer_id": GOLDEN_ID_LABEL, "source_key": "customers.crm_customer_id",
                  "status": GOLDEN_ID_LABEL},
        columns={"golden_customer_id": ("golden_customer_id", "VARCHAR"),
                 "crm_customer_id": ("source_key", "VARCHAR"),
                 "xref_status": ("status", "VARCHAR")},
    ),
    # Account -> person, for every product system (static: which person holds an account does not change).
    "account_owner": Source(
        table="c360_account",
        schema="gold",
        use="features",
        label_table="account_monthly_snapshot",
        label_as={"golden_customer_id": GOLDEN_ID_LABEL, "product": "account_monthly_snapshot.product_type"},
        columns={**_c("account_id", "golden_customer_id", "product")},
    ),
    "account_snapshots": Source(
        table="account_monthly_snapshot",
        use="features",
        event_ts="month_end_date",
        # the account holder (gold.c360_account), not the snapshot's CRM ID, 2.6% of which do not exist (DQ R6)
        joins=(Join("o", "c360_account", "o.account_id = t.account_id", schema="gold"),),
        label_as={"o.golden_customer_id": GOLDEN_ID_LABEL},
        columns={
            **_c("account_id", "crm_customer_id", "product_type", "account_status", "dpd_bucket"),
            "golden_customer_id": ("o.golden_customer_id", "VARCHAR"),
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
        joins=(CASE_PERSON, CRM_PERSON),
        label_as={RECORD_PERSON: GOLDEN_ID_LABEL},
        columns={
            **_c("contact_id", "case_id", "crm_customer_id", "account_id", "direction", "treatment_code",
                 "test_cell", "bucket_at_contact"),
            "golden_customer_id": (RECORD_PERSON, "VARCHAR"),
            # channel_resolved = coalesce(channel_v2, channel): `channel` is empty from 26 Sep 2026 (REL-0926, data
            # quality check K1). Derived in curated by factory/quality/dq_report.py, so not available on raw.
            "channel": ("channel_resolved", "VARCHAR"),
            # local time: ptp_created_ts is recorded in customer-local time too
            "contact_ts": ("contact_ts_local", "TIMESTAMP"),
            **_c("rpc_flag", "ptp_made_flag", "email_delivered_flag", "email_opened_flag", "email_clicked_flag",
                 "sms_delivered_flag", "link_clicked_flag", "push_delivered_flag", "push_opened_flag",
                 "payment_taken_flag", type_="BOOLEAN"),
            **_c("payment_amount", "contact_cost_cad", type_="DOUBLE"),
            "dpd_at_contact": ("dpd_at_contact", "INTEGER"),
        },
    ),
    # Case facts known at a point in time, for the case-level model. The day-k arrears are only known k days
    # after the case opened; the outcome columns are labels only, masked until they happen.
    "case_history": Source(
        table="collections_cases",
        use="features",
        event_ts="case_open_ts",
        joins=(CASE_PERSON,),
        label_as={"cc.golden_customer_id": GOLDEN_ID_LABEL},
        columns={
            **_c("case_id", "primary_account_id", "primary_product", "case_open_reason"),
            "crm_customer_id": ("coll_customer_ref", "VARCHAR"),
            "golden_customer_id": ("cc.golden_customer_id", "VARCHAR"),
            "case_open_ts": ("case_open_date", "TIMESTAMP"),
            **_c("entry_dpd", "accounts_in_case_count", "prior_cases_12m", type_="INTEGER"),
            "entry_overdue": ("entry_overdue", "DOUBLE"),
            **{f"dpd_at_day_{k}": (f"dpd_at_day_{k}", "INTEGER") for k in CASE_DAYS},
            **{f"overdue_at_day_{k}": (f"overdue_at_day_{k}", "DOUBLE") for k in CASE_DAYS},
            # outcome: label only
            **_c("cure_date", "case_status_date", type_="DATE"),
            "case_status": ("case_status", "VARCHAR"),
        },
        known_at={
            **{f"{p}_at_day_{k}": (f'"case_open_ts" + INTERVAL {k} DAY', "NULL") for p in ("dpd", "overdue")
               for k in CASE_DAYS},
            "cure_date": ('"cure_date"', "NULL"),
            "case_status": ('"case_status_date"', "'open'"),
            "case_status_date": ('"case_status_date"', "NULL"),
        },
    ),
    "cases": Source(
        table="collections_cases",
        use="rules",
        event_ts="case_open_date",
        joins=(CASE_PERSON,),
        label_as={"cc.golden_customer_id": GOLDEN_ID_LABEL},
        columns={
            **_c("case_id", "case_status", "primary_account_id", "primary_product", "test_cell", "strategy_code",
                 "assignment_method", "dispute_status", "current_bucket", "current_ptp_id", "assigned_agent_id",
                 "assigned_team"),
            "crm_customer_id": ("coll_customer_ref", "VARCHAR"),
            "golden_customer_id": ("cc.golden_customer_id", "VARCHAR"),
            **_c("case_open_date", "outcome_date", type_="DATE"),
            **_c("entry_dpd", "current_dpd", "contact_cap_7d", "contacts_last_7d", type_="INTEGER"),
            **_c("total_overdue", "entry_overdue", "payments_since_open_amount", type_="DOUBLE"),
            **_c("hardship_flag", "dispute_flag", "cease_contact_flag", "third_party_rep_flag", "insolvency_hold_flag",
                 "deceased_hold_flag", "vulnerable_customer_flag", "cure_flag", type_="BOOLEAN"),
            # hardship handling so far (nba/hardship.py): who recorded it, why, and any programme or plan
            **_c("hardship_source", "hardship_reason"),
            **_c("hardship_program_offered", "hardship_program_accepted", type_="BOOLEAN"),
            "hardship_plan_end": ("hardship_plan_end", "DATE"),
        },
    ),
    # Hardship programme offers and the customer's answer (nba/hardship.py: offered recently? plan broken? uses
    # in 12 months?). Some offers and responses are dated after the snapshot: callers keep offer_ts < as-of.
    "hardship_offers": Source(
        table="offers",
        use="rules",
        event_ts="offer_ts",
        columns={
            **_c("offer_id", "case_id", "program_id", "customer_response", "plan_status"),
            **_c("offer_ts", "response_ts", type_="TIMESTAMP"),
        },
    ),
    # When each account was opened, for programme eligibility (min_months_on_book). One row per account.
    "card_opened": Source(
        table="card_accounts",
        use="rules",
        columns={"account_id": ("card_account_id", "VARCHAR"), "opened": ("open_date", "DATE")},
    ),
    "loan_opened": Source(
        table="loan_accounts",
        use="rules",
        columns={"account_id": ("loan_id", "VARCHAR"), "opened": ("origination_date", "DATE")},
    ),
    "deposit_opened": Source(
        table="deposit_accounts",
        use="rules",
        columns={"account_id": ("deposit_account_id", "VARCHAR"), "opened": ("open_date", "DATE")},
    ),
    "hardship_programs": Source(
        table="hardship_programs",
        use="rules",
        columns={
            **_c("program_id", "version", "program_name", "min_hardship_level", "approver_level", "policy_section"),
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
    # The bank's own view of its models' health (governance/monitoring.py): stated validation AUC and monthly PSI.
    "bank_model_health": Source(
        table="model_scores",
        use="evaluation",
        columns={
            **_c("model_name", "model_version"),
            "score_date": ("score_date", "DATE"),
            **_c("psi_last_month", "auc_at_validation", type_="DOUBLE"),
        },
    ),
    # Safety flags: nba/rules.py ONLY (P3 plan §4 step 6). Never a model feature. One row per CRM record, carrying
    # its PERSON's safeguards (gold.customer_safeguards: most restrictive across all their records, and P1 adds
    # case insolvency holds and vulnerability disclosed on contacts). Not in gold yet, so from the CRM record itself:
    # the preferred contact window. Consents are 'true' / 'false' text in gold.
    "crm_safety_flags": Source(
        table="customer_xref",
        schema="gold",
        use="rules",
        where="t.source_system = 'crm'",
        joins=(Join("s", "customer_safeguards", "s.golden_customer_id = t.golden_customer_id", schema="gold"),
               Join("c", "customers_current", "c.crm_customer_id = t.source_key")),
        label_as={
            "source_key": "customers.crm_customer_id", "golden_customer_id": GOLDEN_ID_LABEL,
            "s.consent_phone": "customers.consent_call", "s.consent_sms": "customers.consent_sms",
            "s.consent_email": "customers.consent_email", "s.consent_push": "customers.consent_push",
            "s.consent_mail": "customers.consent_letter", "s.cease_contact": "customers.cease_communication_flag",
            "s.deceased": "customers.deceased_flag", "s.bankruptcy": "customers.bankruptcy_flag",
            "s.insolvency": "customers.consumer_proposal_flag", "s.vulnerability_flag": "customers.vulnerability_flag",
            "s.internal_do_not_call": "customers.internal_do_not_call_flag",
            "s.legal_representative": "customers.legal_representative_flag",
            "s.sunday_contact_permitted": "customers.sunday_contact_permitted",
            "s.permitted_call_start_local": "customers.permitted_call_start_local",
            "s.permitted_call_end_local": "customers.permitted_call_end_local",
            "c.preferred_contact_window_start": "customers.preferred_contact_window_start",
            "c.preferred_contact_window_end": "customers.preferred_contact_window_end",
        },
        columns={
            "crm_customer_id": ("source_key", "VARCHAR"),
            "golden_customer_id": ("golden_customer_id", "VARCHAR"),
            "consent_call": ("s.consent_phone", "BOOLEAN"),
            "consent_sms": ("s.consent_sms", "BOOLEAN"),
            "consent_email": ("s.consent_email", "BOOLEAN"),
            "consent_push": ("s.consent_push", "BOOLEAN"),
            "consent_letter": ("s.consent_mail", "BOOLEAN"),
            "cease_communication_flag": ("s.cease_contact", "BOOLEAN"),
            "deceased_flag": ("s.deceased", "BOOLEAN"),
            "bankruptcy_flag": ("s.bankruptcy", "BOOLEAN"),
            # consumer proposal, or an insolvency hold on any of the person's cases
            "consumer_proposal_flag": ("s.insolvency", "BOOLEAN"),
            "vulnerability_flag": ("s.vulnerability_flag", "BOOLEAN"),
            "internal_do_not_call_flag": ("s.internal_do_not_call", "BOOLEAN"),
            "legal_representative_flag": ("s.legal_representative", "BOOLEAN"),
            "sunday_contact_permitted": ("s.sunday_contact_permitted", "BOOLEAN"),
            "preferred_contact_window_start": ("c.preferred_contact_window_start", "VARCHAR"),
            "preferred_contact_window_end": ("c.preferred_contact_window_end", "VARCHAR"),
            "permitted_call_start_local": ("s.permitted_call_start_local", "VARCHAR"),
            "permitted_call_end_local": ("s.permitted_call_end_local", "VARCHAR"),
        },
    ),
    # Protected attributes, joined back ONLY in nba/fairness.py to compare outcomes across groups (testing only).
    # Never a feature, never a rule input; the report shows aggregates of groups of at least MIN_GROUP people.
    # preferred_language is not protected, but is checked as a possible stand-in for national origin.
    "protected_attributes": Source(
        table="customers",
        use="fairness",
        where="CAST(is_deleted AS VARCHAR) = 'false' AND CAST(effective_to_ts AS VARCHAR) LIKE '9999%'",
        columns={
            **_c("crm_customer_id", "gender_code", "age_band", "marital_status", "citizenship_status",
                 "preferred_language"),
            **_c("newcomer_program_flag", "accessibility_needs_flag", type_="BOOLEAN"),
            "dependants_count": ("dependants_count", "INTEGER"),
        },
    ),
    # Who may take a hardship / vulnerable case (nba/queue.py routing), and who may approve (nba/review.py).
    "agents": Source(
        table="agents",
        use="rules",
        columns={
            **_c("agent_id", "status", "team_id", "site", "role", "team_lead_id"),
            "vulnerable_customer_certified": ("vulnerable_customer_certified", "BOOLEAN"),
        },
    ),
    # Who is working on a day, and how many outbound calls each shift can make (nba/allocate.py).
    "agent_shifts": Source(
        table="agent_shifts",
        use="rules",
        columns={
            "agent_id": ("agent_id", "VARCHAR"),
            "shift_date": ("shift_date", "DATE"),
            "outbound_call_capacity": ("outbound_call_capacity", "INTEGER"),
            "absence_flag": ("absence_flag", "BOOLEAN"),
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


def _is_sql(src_col: str) -> bool:
    return "(" in src_col


def _expr(src_col: str, type_: str) -> str:
    if _is_sql(src_col):
        col = src_col
    else:
        alias, _, name = src_col.rpartition(".")
        col = f'{alias or "t"}."{name}"'
    if type_ == "JSON_LIST":
        return f"CAST(from_json({col}, '[\"VARCHAR\"]') AS VARCHAR[])"
    if type_ == "VARCHAR":
        return col
    return f"TRY_CAST({col} AS {type_})"


def sql(name: str, as_of: datetime | str | None = None) -> str:
    """SELECT for one source. With as_of, only what was known strictly before it."""
    s = SOURCES[name]
    typed = ", ".join(f'{_expr(c, t)} AS "{out}"' for out, (c, t) in s.columns.items())
    joins = "".join(f' LEFT JOIN {j.schema}."{j.table}" {j.alias} ON {j.on}' for j in s.joins)
    inner = f'SELECT {typed} FROM {s.schema}."{s.table}" t{joins}' + (f" WHERE {s.where}" if s.where else "")
    if as_of is None or s.event_ts is None:
        return inner
    ts = f"TIMESTAMP '{as_of}'"
    outs = []
    for out in s.columns:
        if out in s.known_at:
            known, before = s.known_at[out]
            outs.append(f'CASE WHEN {known} < {ts} THEN "{out}" ELSE {before} END AS "{out}"')
        else:
            outs.append(f'"{out}"')
    return f'SELECT {", ".join(outs)} FROM ({inner}) WHERE "{s.event_ts}" < {ts}'


def source_tables() -> dict[tuple[str, str], set[str]]:
    """Every (schema, table) the sources read, with the columns they name: select list, join keys and filters."""
    tables: dict[tuple[str, str], set[str]] = {}
    for s in SOURCES.values():
        aliases = {"t": (s.schema, s.table), **{j.alias: (j.schema, j.table) for j in s.joins}}
        tables.setdefault(aliases["t"], set()).update(
            c for c in re.findall(r"(?:CAST\(|\bt\.)?\b(\w+)\s*(?:AS VARCHAR\)\s*)?(?:=|LIKE)", s.where or "")
            if c not in ("t", "VARCHAR"))
        for src_col, _ in s.columns.values():
            for alias, col in (re.findall(r"\b(\w+)\.(\w+)", src_col) if _is_sql(src_col)
                               else [src_col.rpartition(".")[::2]]):
                tables.setdefault(aliases[alias or "t"], set()).add(col)
        for j in s.joins:
            for alias, col in re.findall(r"\b(\w+)\.(\w+)", j.on):
                tables.setdefault(aliases[alias], set()).add(col)
    return tables


def create_empty_tables(con: duckdb.DuckDBPyConnection) -> None:
    """For test fixtures: every source table as all-VARCHAR columns, like load_raw makes them. Tables that already
    exist (the gold interface stubs) get any missing column added."""
    for (schema, table), cols in source_tables().items():
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        ddl = ", ".join(f'"{c}" VARCHAR' for c in sorted(cols))
        con.execute(f'CREATE TABLE IF NOT EXISTS {schema}."{table}" ({ddl})')
        for c in cols:
            con.execute(f'ALTER TABLE {schema}."{table}" ADD COLUMN IF NOT EXISTS "{c}" VARCHAR')


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


def customer_xref(con):
    return relation(con, "customer_xref")


def account_owner(con):
    return relation(con, "account_owner")


def crm_safety_flags(con):
    """Rules only (nba/rules.py). Never a model feature."""
    return relation(con, "crm_safety_flags")


def channel_capacity(con):
    return relation(con, "channel_capacity")


def promise_outcomes(con):
    """Action valuation only (nba/actions.py). Never a feature."""
    return relation(con, "promise_outcomes")


def agents(con):
    return relation(con, "agents")


def case_history(con, as_of=None):
    return relation(con, "case_history", as_of)


def agent_shifts(con):
    return relation(con, "agent_shifts")


def hardship_offers(con):
    """Rules only (nba/hardship.py). Some offers are dated after the snapshot: keep offer_ts < as-of."""
    return relation(con, "hardship_offers")


def card_opened(con):
    return relation(con, "card_opened")


def loan_opened(con):
    return relation(con, "loan_opened")


def deposit_opened(con):
    return relation(con, "deposit_opened")


def protected_attributes(con):
    """nba/fairness.py ONLY: protected attributes for comparing outcomes across groups. Never a feature."""
    return relation(con, "protected_attributes")


def bank_model_health(con):
    """governance/monitoring.py: the bank's stated validation AUC and monthly PSI for each of its models."""
    return relation(con, "bank_model_health")
