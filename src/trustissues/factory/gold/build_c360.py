"""Gold Collections 360, built into gold_stage (publish.py moves it to gold after the quality gate):
c360_account (one row per account), case_customer (who each case belongs to), c360_customer (one row per person).

Reads only curated.* and gold_stage.customer_xref. Shapes match contracts/interfaces/stubs.sql (extra columns allowed;
publish.py checks the agreed columns and types are all present).

Survivorship for identity fields (design §5): CRM wins over product systems; among a person's CRM records the
KYC-complete one wins, then the most recently updated. Without CRM, the most recently updated product record
wins; bureau-reported identity is the last resort and never overrides bank data. Every survived field stores
<field>_source and <field>_as_of.

Safety flags: any source is enough (no survivorship). Consent: the most restrictive value across the person's
CRM records. A collections case is attached to the person named by its CRM ID AND to the holders of the
accounts it lists. In about 0.3% of cases these are two different people (the case's CRM ID is one character off
the account holder's, almost certainly a typo); safety and hardship flags from such a case reach both people (the
protective reading). gold_stage.case_customer records both readings.

`total_balance` is money OWED: card balance, loan outstanding, and deposit overdraft in use. Deposit credit
balances are assets and are not netted against debt. `max_dpd` / `worst_bucket` are over open accounts.
"""
from __future__ import annotations

import time

from trustissues.common.db import connect

BUCKETS = ["current", "1-30", "31-60", "61-90", "91-120", "121-150", "151-180", "180+"]
_bucket_rank = "CASE {c} " + " ".join(f"WHEN '{b}' THEN {i}" for i, b in enumerate(BUCKETS)) + " END"


def bucket_rank(col: str) -> str:
    return _bucket_rank.format(c=col)


ACCOUNTS_SQL = """
    SELECT c.card_account_id AS account_id, x.golden_customer_id, 'card' AS product_system, 'credit_card' AS product,
           c.snapshot_date, c.current_balance AS balance, c.past_due_amount AS overdue_amount, c.dpd::INTEGER AS dpd,
           c.dpd_bucket AS bucket, c.account_status AS status,
           c.current_balance AS amount_owed, c.credit_limit, c.hardship_program_flag AS hardship_flag,
           c.dispute_open_flag AS dispute_flag, c.chargeoff_flag, 'card' AS source_system, c.src_customer_ref AS source_key
    FROM curated.card_accounts_current c JOIN gold_stage.customer_xref x ON x.node_id = 'card|' || c.src_customer_ref
    UNION ALL
    SELECT l.loan_id, x.golden_customer_id, 'loan', l.product_type, l.snapshot_date, l.total_outstanding, l.past_due_amount,
           l.dpd::INTEGER, l.dpd_bucket,
           CASE WHEN l.chargeoff_flag THEN 'charged_off' WHEN coalesce(l.total_outstanding, 0) <= 0 THEN 'paid_off' ELSE 'open' END,
           l.total_outstanding, l.original_amount, l.hardship_program_flag, false, l.chargeoff_flag, 'loan', l.src_customer_ref
    FROM curated.loan_accounts_current l JOIN gold_stage.customer_xref x ON x.node_id = 'loan|' || l.src_customer_ref
    UNION ALL
    SELECT d.deposit_account_id, x.golden_customer_id, 'deposit', d.product_type, d.snapshot_date, d.current_balance,
           CASE WHEN coalesce(d.overdraft_dpd, 0) > 0 THEN d.overdraft_used ELSE 0 END,
           d.overdraft_dpd::INTEGER, d.overdraft_dpd_bucket, d.account_status,
           coalesce(d.overdraft_used, 0), d.overdraft_limit, false, false, false, 'deposit', d.cif10
    FROM curated.deposit_accounts_current d JOIN gold_stage.customer_xref x ON x.node_id = 'deposit|' || d.cif10
"""


def build_accounts(con) -> int:
    con.execute(f"CREATE OR REPLACE TABLE gold_stage.c360_account AS {ACCOUNTS_SQL}")
    return con.execute("SELECT count(*) FROM gold_stage.c360_account").fetchone()[0]


def build_case_customer(con) -> dict:
    """gold_stage.case_customer: who each collections case belongs to, with both readings kept.

    named_*   : the customer in coll_customer_ref (copied from CRM when the case opened)
    account_* : the holder(s) of the accounts the case lists
    golden_customer_id: the account holder when the accounts name exactly one person, else the named customer.
    When the two disagree (case_customer_mismatch) the IDs are one character apart in every case seen, and the
    case's own province matches the account holder in 433 of 436 checkable cases: the named ID is almost
    certainly mistyped. Default chosen: the account holder. Both readings stay available.
    """
    con.execute("""CREATE OR REPLACE TABLE gold_stage.case_customer AS
        WITH ca AS (SELECT case_id, trim(unnest(CASE WHEN account_ids LIKE '[%' THEN from_json(account_ids, '["VARCHAR"]')
                                                     ELSE string_split(account_ids, ';') END)) AS account_id
                    FROM curated.collections_cases),
        holders AS (SELECT ca.case_id, list(DISTINCT a.golden_customer_id ORDER BY a.golden_customer_id) AS account_golden_customer_ids
                    FROM ca JOIN gold_stage.c360_account a USING (account_id) GROUP BY 1)
        SELECT c.case_id, c.coll_customer_ref AS named_crm_customer_id, x.golden_customer_id AS named_golden_customer_id,
               h.account_golden_customer_ids,
               CASE WHEN len(h.account_golden_customer_ids) = 1 THEN h.account_golden_customer_ids[1]
                    ELSE x.golden_customer_id END AS golden_customer_id,
               CASE WHEN len(h.account_golden_customer_ids) = 1 THEN 'account_holder'
                    WHEN x.golden_customer_id IS NOT NULL THEN 'named_customer' END AS resolved_by,
               x.golden_customer_id IS NOT NULL AND h.account_golden_customer_ids IS NOT NULL
                   AND NOT list_contains(h.account_golden_customer_ids, x.golden_customer_id) AS case_customer_mismatch,
               x.golden_customer_id IS NULL AS named_customer_missing
        FROM curated.collections_cases c
        LEFT JOIN gold_stage.customer_xref x ON x.node_id = 'crm|' || c.coll_customer_ref
        LEFT JOIN holders h USING (case_id)""")
    return dict(zip(["cases", "mismatch", "named_customer_missing", "unresolved"], con.execute(
        """SELECT count(*), count(*) FILTER (WHERE case_customer_mismatch), count(*) FILTER (WHERE named_customer_missing),
                  count(*) FILTER (WHERE golden_customer_id IS NULL) FROM gold_stage.case_customer""").fetchone()))


def build_customers(con) -> int:
    # a case's flags reach the named customer AND the account holders (protective when the two disagree)
    con.execute("""CREATE OR REPLACE TEMP TABLE case_people AS
        SELECT case_id, named_golden_customer_id AS golden_customer_id FROM gold_stage.case_customer WHERE named_golden_customer_id IS NOT NULL
        UNION SELECT case_id, unnest(account_golden_customer_ids) FROM gold_stage.case_customer WHERE account_golden_customer_ids IS NOT NULL""")
    con.execute("""CREATE OR REPLACE TEMP TABLE case_flags AS
        SELECT p.golden_customer_id,
               bool_or(coalesce(c.cease_contact_flag, false)) AS cease, bool_or(coalesce(c.deceased_hold_flag, false)) AS deceased,
               bool_or(c.dispute_status = 'open') AS dispute, bool_or(coalesce(c.hardship_flag, false)) AS hardship,
               bool_or(coalesce(c.vulnerable_customer_flag, false)) AS vulnerable
        FROM case_people p JOIN curated.collections_cases c USING (case_id) GROUP BY 1""")
    con.execute(f"""CREATE OR REPLACE TABLE gold_stage.c360_customer AS
    WITH xr AS (SELECT * FROM gold_stage.customer_xref),
    crm AS (SELECT x.golden_customer_id, c.* FROM xr x JOIN curated.customers_current c
            ON x.source_system = 'crm' AND c.crm_customer_id = x.source_key),
    crm_best AS (SELECT * FROM crm QUALIFY row_number() OVER (PARTITION BY golden_customer_id
                 ORDER BY kyc_status = 'complete' DESC, last_profile_update_ts DESC NULLS LAST, crm_customer_id) = 1),
    other_best AS (  -- best non-CRM identity: products by recency, bureau last
        SELECT x.golden_customer_id, n.source_system, n.as_of_ts,
               concat_ws(' ', n.first_name, n.middle_initials, n.last_name) AS full_name, n.dob
        FROM xr x JOIN identity.nodes n ON n.node_id = x.node_id WHERE n.source_system <> 'crm'
        QUALIFY row_number() OVER (PARTITION BY x.golden_customer_id
                ORDER BY n.source_system = 'bureau', n.first_is_initial, n.as_of_ts DESC NULLS LAST, n.node_id) = 1),
    crm_agg AS (SELECT golden_customer_id,
               -- most restrictive consent across CRM duplicates: any false wins
               bool_and(consent_call) consent_call, bool_and(consent_sms) consent_sms, bool_and(consent_email) consent_email,
               bool_and(consent_letter) consent_letter, bool_and(consent_push) consent_push, bool_and(consent_autodialer) consent_autodialer,
               bool_or(coalesce(cease_communication_flag, false)) cease, bool_or(coalesce(deceased_flag, false)) deceased,
               bool_or(coalesce(bankruptcy_flag, false)) bankruptcy
        FROM crm GROUP BY 1),
    bureau AS (SELECT x.golden_customer_id, bool_or(coalesce(e.deceased_indicator, false)) deceased,
               bool_or(coalesce(e.bankruptcy_on_file_flag, false)) bankruptcy
        FROM xr x JOIN curated.external e ON x.source_system = 'bureau' AND e.bank_subject_ref = x.source_key GROUP BY 1),
    contact AS (SELECT x.golden_customer_id, bool_or(coalesce(h.cease_request_flag, false)) cease
        FROM curated.contact_history h JOIN xr x ON x.node_id = 'crm|' || h.crm_customer_id GROUP BY 1),
    acct AS (SELECT golden_customer_id, count(*) product_count,
               sum(amount_owed) total_balance, sum(coalesce(overdue_amount, 0)) total_overdue,
               max(dpd) FILTER (WHERE status NOT IN ('closed', 'paid_off')) max_dpd,
               arg_max(bucket, {bucket_rank('bucket')}) FILTER (WHERE status NOT IN ('closed', 'paid_off')) worst_bucket,
               bool_or(hardship_flag) hardship, bool_or(dispute_flag) dispute
        FROM gold_stage.c360_account GROUP BY 1),
    payroll AS (SELECT x.golden_customer_id, max_by(d.payroll_delay_days, d.snapshot_date) payroll_delay_days
        FROM curated.deposit_accounts_current d JOIN xr x ON x.node_id = 'deposit|' || d.cif10 GROUP BY 1),
    promise AS (SELECT coalesce(cc.golden_customer_id, x.golden_customer_id) golden_customer_id,
               max_by(p.ptp_status, p.ptp_created_ts) active_promise_status
        FROM curated.promises_to_pay p LEFT JOIN gold_stage.case_customer cc USING (case_id)
        LEFT JOIN xr x ON x.node_id = 'crm|' || p.crm_customer_id
        WHERE coalesce(cc.golden_customer_id, x.golden_customer_id) IS NOT NULL GROUP BY 1),
    lineage AS (SELECT golden_customer_id,
               to_json(map_from_entries(list((source_system, keys))))::VARCHAR source_keys,
               array_to_string(list_sort(list(source_system)), ',') source_systems
        FROM (SELECT golden_customer_id, source_system, list_sort(list(source_key)) keys FROM xr GROUP BY 1, 2) GROUP BY 1),
    links AS (SELECT golden_customer_id,
               arg_min(match_method, CASE match_method WHEN 'crm_pointer' THEN 0 WHEN 'national_id' THEN 1 WHEN 'cif' THEN 2
                   WHEN 'human' THEN 3 WHEN 'name_dob_phone' THEN 4 WHEN 'dob_name_postal' THEN 5 WHEN 'splink' THEN 6
                   WHEN 'llm_confirmed' THEN 7 ELSE 9 END) match_method,
               min(match_confidence) match_confidence, sum(open_review_items) open_review_items
        FROM xr GROUP BY 1)
    SELECT g.golden_customer_id, l.source_keys, k.match_method, k.match_confidence,
           coalesce(nullif(concat_ws(' ', cb.first_name, cb.middle_name, cb.last_name), ''), ob.full_name) AS full_name,
           coalesce(cb.date_of_birth, ob.dob) AS date_of_birth,
           cb.segment, cb.province_code AS province,
           ca.consent_call::VARCHAR AS consent_phone, ca.consent_sms::VARCHAR AS consent_sms,
           ca.consent_email::VARCHAR AS consent_email, ca.consent_letter::VARCHAR AS consent_mail,
           coalesce(ca.cease, false) OR coalesce(cf.cease, false) OR coalesce(ct.cease, false) AS cease_contact,
           coalesce(ca.deceased, false) OR coalesce(cf.deceased, false) OR coalesce(b.deceased, false) AS deceased,
           coalesce(ca.bankruptcy, false) OR coalesce(b.bankruptcy, false) AS bankruptcy,
           coalesce(cf.dispute, false) OR coalesce(a.dispute, false) AS open_dispute,
           coalesce(cf.hardship, false) OR coalesce(a.hardship, false) AS hardship_flag,
           coalesce(a.total_balance, 0) AS total_balance, coalesce(a.total_overdue, 0) AS total_overdue,
           a.max_dpd::INTEGER AS max_dpd, a.worst_bucket, pr.active_promise_status,
           l.source_systems, current_timestamp::TIMESTAMP AS refresh_ts,
           -- extras beyond the stub
           ca.consent_push::VARCHAR AS consent_push, ca.consent_autodialer::VARCHAR AS consent_autodialer,
           coalesce(a.product_count, 0)::INTEGER AS product_count, py.payroll_delay_days,
           CASE WHEN cb.crm_customer_id IS NOT NULL THEN 'crm' ELSE ob.source_system END AS full_name_source,
           CASE WHEN cb.crm_customer_id IS NOT NULL THEN coalesce(cb.last_profile_update_ts, cb.extract_ts) ELSE ob.as_of_ts END AS full_name_as_of,
           CASE WHEN cb.date_of_birth IS NOT NULL THEN 'crm' WHEN ob.dob IS NOT NULL THEN ob.source_system END AS date_of_birth_source,
           CASE WHEN cb.date_of_birth IS NOT NULL THEN coalesce(cb.last_profile_update_ts, cb.extract_ts) WHEN ob.dob IS NOT NULL THEN ob.as_of_ts END AS date_of_birth_as_of,
           cb.crm_customer_id AS survivor_crm_customer_id,
           CASE WHEN k.open_review_items > 0 THEN 'identity_in_review' ELSE 'ok' END AS dq_status
    FROM (SELECT DISTINCT golden_customer_id FROM xr) g
    JOIN lineage l USING (golden_customer_id) JOIN links k USING (golden_customer_id)
    LEFT JOIN crm_best cb USING (golden_customer_id) LEFT JOIN other_best ob USING (golden_customer_id)
    LEFT JOIN crm_agg ca USING (golden_customer_id) LEFT JOIN bureau b USING (golden_customer_id)
    LEFT JOIN contact ct USING (golden_customer_id) LEFT JOIN case_flags cf USING (golden_customer_id)
    LEFT JOIN acct a USING (golden_customer_id) LEFT JOIN payroll py USING (golden_customer_id)
    LEFT JOIN promise pr USING (golden_customer_id)""")
    return con.execute("SELECT count(*) FROM gold_stage.c360_customer").fetchone()[0]


def build(con) -> dict:
    out = {"c360_account": build_accounts(con), "case_customer": build_case_customer(con),
           "c360_customer": build_customers(con)}
    dupes = con.execute("SELECT count(*) - count(DISTINCT golden_customer_id) FROM gold_stage.c360_customer").fetchone()[0]
    if dupes:
        raise ValueError(f"gold_stage.c360_customer is not one row per person: {dupes} duplicate ids")
    return out


def run() -> dict:
    t0 = time.time()
    con = connect()
    out = build(con)
    con.close()
    out["secs"] = round(time.time() - t0)
    return out


def main() -> None:
    print(run())


if __name__ == "__main__":
    main()
