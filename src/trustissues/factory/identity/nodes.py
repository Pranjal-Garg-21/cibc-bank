"""Step 2 of identity: one node per source CUSTOMER (not per account), all sources stacked in one shape.

    identity.nodes: node_id = '<source_system>|<source_key>'

| source_system | source key                             | one node per          |
|---------------|----------------------------------------|-----------------------|
| crm           | customers.crm_customer_id              | current CRM record    |
| card          | card_accounts.src_customer_ref         | card customer         |
| loan          | loan_accounts.src_customer_ref         | borrower              |
| deposit       | deposit_accounts.cif10                 | CIF (10 digits)       |
| bureau        | external.bank_subject_ref              | CIF (10 digits)       |

A product customer with several accounts can carry slightly different identity values on each account
(a full first name on one card, an initial on another, a name written surname-first on a third). The node takes
the whole name from one representative account (the fullest first name; never first from one account and
surname from another), the most common DOB and postal code, and every full phone it has seen.
`n_dobs` > 1 records that the source itself disagrees about the DOB.
"""
from __future__ import annotations

from trustissues.common.db import connect
from trustissues.factory.curate import standardise as S

# Columns every node carries. Missing values stay NULL, never guessed.
NODE_COLUMNS = """
    node_id VARCHAR, source_system VARCHAR, source_key VARCHAR,
    first_name VARCHAR, first_initial VARCHAR, first_is_initial BOOLEAN, middle_initials VARCHAR,
    last_name VARCHAR, name_key VARCHAR,
    dob DATE, n_dobs INTEGER,
    phones VARCHAR[], phone_masked_prefix VARCHAR, phone_masked_suffix VARCHAR,
    postal VARCHAR, fsa VARCHAR, employer VARCHAR,
    national_id_hash VARCHAR, crm_duplicate_of VARCHAR, record_created_ts TIMESTAMP, as_of_ts TIMESTAMP,
    n_records INTEGER
"""


def _product_node_sql(system: str, table: str, key: str, ts: str, employer: str = "NULL") -> str:
    """Aggregate a product table (one row per account) to one row per source customer."""
    return f"""
    SELECT '{system}|' || {key} AS node_id, '{system}' AS source_system, {key} AS source_key,
           -- the whole name comes from ONE representative account (fullest first name), never mixed across accounts
           max_by(std_name_first, (length(std_name_first), std_name_last)) AS first_name,
           left(max_by(std_name_first, (length(std_name_first), std_name_last)), 1) AS first_initial,
           length(max_by(std_name_first, (length(std_name_first), std_name_last))) = 1 AS first_is_initial,
           max_by(std_name_middle_initials, (length(std_name_first), std_name_last)) AS middle_initials,
           max_by(std_name_last, (length(std_name_first), std_name_last)) AS last_name,
           max_by(std_name_name_key, (length(std_name_first), std_name_last)) AS name_key,
           TRY_CAST(mode(std_dob) AS DATE) AS dob, count(DISTINCT std_dob)::INTEGER AS n_dobs,
           list_distinct(list(std_phone_primary_e164) FILTER (WHERE std_phone_primary_e164 IS NOT NULL)) AS phones,
           max(std_phone_primary_prefix) FILTER (WHERE std_phone_primary_masked) AS phone_masked_prefix,
           max(std_phone_primary_suffix) FILTER (WHERE std_phone_primary_masked) AS phone_masked_suffix,
           mode(std_postal) AS postal, mode(std_fsa) AS fsa, {employer} AS employer,
           NULL::VARCHAR AS national_id_hash, NULL::VARCHAR AS crm_duplicate_of,
           NULL::TIMESTAMP AS record_created_ts, max({ts})::TIMESTAMP AS as_of_ts,
           count(*)::INTEGER AS n_records
    FROM curated.{table}
    WHERE {key} IS NOT NULL
    GROUP BY {key}"""


SOURCES = {
    "crm": """
    SELECT 'crm|' || crm_customer_id, 'crm', crm_customer_id,
           std_name_first, left(std_name_first, 1), std_name_first_is_initial, std_name_middle_initials,
           std_name_last, std_name_name_key,
           TRY_CAST(std_dob AS DATE), CASE WHEN std_dob IS NULL THEN 0 ELSE 1 END,
           list_distinct([p FOR p IN [std_phone_primary_e164, std_phone_secondary_e164, std_phone_work_e164] IF p IS NOT NULL]),
           NULL, NULL,
           std_postal, std_fsa, std_employer,
           national_id_hash, duplicate_of_crm_id, record_created_ts,
           coalesce(last_profile_update_ts, extract_ts)::TIMESTAMP, 1
    FROM curated.customers_current""",
    "card": _product_node_sql("card", "card_accounts_current", "src_customer_ref", "snapshot_date"),
    "loan": _product_node_sql("loan", "loan_accounts_current", "src_customer_ref", "snapshot_date"),
    # payroll descriptors ('BOREAL CARE HOMES PAY') are the only employer evidence outside CRM
    "deposit": _product_node_sql("deposit", "deposit_accounts_current", "cif10", "snapshot_date",
                                 employer="std_employer(regexp_replace(mode(payroll_descriptor_raw), '\\s+PAY$', ''))"),
    "bureau": """
    SELECT 'bureau|' || bank_subject_ref, 'bureau', bank_subject_ref,
           std_name_first, left(std_name_first, 1), std_name_first_is_initial, std_name_middle_initials,
           std_name_last, std_name_name_key,
           TRY_CAST(std_dob AS DATE), CASE WHEN std_dob IS NULL THEN 0 ELSE 1 END,
           []::VARCHAR[], NULL, NULL,
           std_postal, std_fsa, NULL,
           NULL, NULL, NULL, coalesce(file_received_ts, pull_date)::TIMESTAMP, 1
    FROM curated.external
    WHERE bank_subject_ref IS NOT NULL""",
}


def build(con) -> dict:
    con.create_function("std_employer", S.normalise_employer, ["VARCHAR"], "VARCHAR", side_effects=False)
    con.execute(f"CREATE OR REPLACE TABLE identity.nodes ({NODE_COLUMNS})")
    counts = {}
    for system, sql in SOURCES.items():
        con.execute(f"INSERT INTO identity.nodes {sql}")
        counts[system] = con.execute("SELECT count(*) FROM identity.nodes WHERE source_system = ?", [system]).fetchone()[0]
    dupes = con.execute("SELECT count(*) - count(DISTINCT node_id) FROM identity.nodes").fetchone()[0]
    if dupes:
        raise ValueError(f"identity.nodes has {dupes} duplicate node_id values")
    return counts


def main() -> None:
    con = connect()
    print(build(con))
    con.close()


if __name__ == "__main__":
    main()
