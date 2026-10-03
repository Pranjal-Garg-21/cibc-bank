"""Consumer views (plan §3.4), built into gold_stage and published to gold by publish.py.

    promises_enriched     P3, P2   one row per promise; ptp_created_ts is the point-in-time key
    customer_safeguards   P3       hard stops, vulnerability, consent: rules and routing only, never model inputs
    decisioning_customer  P3       allow-list history table, the only source the feature store reads
    customer_timeline     P4       contacts, promises, notes, transcripts, offers, payments in time order
    bucket_transitions    P2       each account's bucket at the start and end of each quarter (roll rate)

They are materialised as tables (millions of rows, read many times). Shapes follow contracts/interfaces/stubs.sql;
extra columns are allowed. Defaults chosen where the team had not decided are marked DEFAULT and listed in
docs/consumer_views.md.
"""
from __future__ import annotations

import time

from trustissues.common import config
from trustissues.common.db import connect

from .build_c360 import bucket_rank

CREDIT_PRODUCTS = "('card', 'unsecured_loc', 'student_loc', 'auto_loan', 'mortgage', 'personal_loan')"


def _as_of_end() -> str:
    return f"TIMESTAMP '{config.AS_OF_DATE.isoformat()} 23:59:59'"


# A collections record (promise, contact, note, transcript, offer) belongs to the case's resolved customer
# (gold_stage.case_customer: the account holder when the two readings disagree), else to its own CRM ID.
def _owner(alias: str) -> str:
    return f"coalesce(cc_{alias}.golden_customer_id, x_{alias}.golden_customer_id)"


def _owner_joins(alias: str) -> str:
    return (f"LEFT JOIN gold_stage.case_customer cc_{alias} ON cc_{alias}.case_id = {alias}.case_id "
            f"LEFT JOIN gold_stage.customer_xref x_{alias} ON x_{alias}.node_id = 'crm|' || {alias}.crm_customer_id")


def promises_enriched(con) -> int:
    # DEFAULT: label_broken follows the certified broken_promise_rate: 'broken' is broken; 'kept' and
    # 'partially_kept' are not; 'open' and 'cancelled' have no label (NULL).
    con.execute(f"""CREATE OR REPLACE TABLE gold_stage.promises_enriched AS
        SELECT p.ptp_id, {_owner('p')} AS golden_customer_id, p.case_id, p.account_id, p.product, p.agent_id, p.ptp_channel,
               p.ptp_created_ts, p.ptp_amount, p.ptp_due_date, p.days_to_due_at_creation::INTEGER AS days_to_due_at_creation,
               p.ptp_type, p.ptp_sequence_in_case::INTEGER AS ptp_sequence_in_case,
               p.prior_broken_ptp_count::INTEGER AS prior_broken_ptp_count, p.overdue_at_creation, p.ptp_to_overdue_ratio,
               p.dpd_at_creation::INTEGER AS dpd_at_creation, p.payment_method_promised, p.grace_days::INTEGER AS grace_days,
               p.customer_stated_income_date,
               -- ===== OUTCOME columns: label / evaluation only, never features =====
               p.ptp_status, p.status_ts, p.amount_paid_against, p.paid_date, p.broken_reason_agent_coded,
               CASE p.ptp_status WHEN 'broken' THEN true WHEN 'kept' THEN false WHEN 'partially_kept' THEN false END AS label_broken,
               -- extras
               p.crm_customer_id, coalesce(cc_p.resolved_by, 'crm_customer_id') AS owner_resolved_by,
               coalesce(cc_p.case_customer_mismatch, false) AS case_customer_mismatch,
               p.dq_kept_but_underpaid
        FROM curated.promises_to_pay p {_owner_joins('p')}""")
    return con.execute("SELECT count(*) FROM gold_stage.promises_enriched").fetchone()[0]


def customer_safeguards(con) -> int:
    # DEFAULT: safety_flag = any hard stop (cease contact, deceased, bankruptcy, open dispute), the four the rules
    # engine must never miss. vulnerability: CRM flag, a case flag, or a disclosure on a contact; accessibility: CRM.
    con.execute(f"""CREATE OR REPLACE TABLE gold_stage.customer_safeguards AS
        WITH crm AS (SELECT x.golden_customer_id,
                    bool_or(coalesce(c.vulnerability_flag, false)) vulnerable, bool_or(coalesce(c.accessibility_needs_flag, false)) accessible,
                    bool_or(coalesce(c.third_party_authorised_flag, false)) third_party, bool_or(coalesce(c.power_of_attorney_flag, false)) poa,
                    bool_or(coalesce(c.legal_representative_flag, false)) legal_rep, bool_or(coalesce(c.credit_counselling_agency_flag, false)) counselling,
                    bool_or(coalesce(c.fraud_victim_flag, false)) fraud_victim, bool_or(coalesce(c.consumer_proposal_flag, false)) consumer_proposal,
                    bool_or(coalesce(c.national_dncl_flag, false)) dncl, bool_or(coalesce(c.internal_do_not_call_flag, false)) dnc,
                    bool_and(coalesce(c.voicemail_permitted, true)) voicemail_ok, bool_and(coalesce(c.contact_at_work_permitted, true)) work_ok,
                    bool_and(coalesce(c.sunday_contact_permitted, true)) sunday_ok,
                    max(c.permitted_call_start_local) call_start, min(c.permitted_call_end_local) call_end,
                    arg_max(c.preferred_language, c.last_profile_update_ts) preferred_language
                FROM gold_stage.customer_xref x JOIN curated.customers_current c ON x.source_system = 'crm' AND c.crm_customer_id = x.source_key
                GROUP BY 1),
        cases AS (SELECT p.golden_customer_id, bool_or(coalesce(c.vulnerable_customer_flag, false)) vulnerable,
                    bool_or(coalesce(c.third_party_rep_flag, false)) third_party, bool_or(coalesce(c.insolvency_hold_flag, false)) insolvency,
                    arg_max(c.language_of_service, c.case_open_date) language_of_service
                FROM (SELECT case_id, named_golden_customer_id golden_customer_id FROM gold_stage.case_customer WHERE named_golden_customer_id IS NOT NULL
                      UNION SELECT case_id, unnest(account_golden_customer_ids) FROM gold_stage.case_customer WHERE account_golden_customer_ids IS NOT NULL) p
                JOIN curated.collections_cases c USING (case_id) GROUP BY 1),
        contacts AS (SELECT {_owner('h')} golden_customer_id, bool_or(coalesce(h.vulnerability_disclosed_flag, false)) vulnerable
                FROM curated.contact_history h {_owner_joins('h')} WHERE h.contact_ts_local <= {_as_of_end()} GROUP BY 1)
        SELECT g.golden_customer_id, g.cease_contact, g.deceased, g.bankruptcy, g.open_dispute, g.hardship_flag,
               coalesce(crm.vulnerable, false) OR coalesce(cases.vulnerable, false) OR coalesce(contacts.vulnerable, false) AS vulnerability_flag,
               coalesce(crm.accessible, false) AS accessibility_flag,
               g.cease_contact OR g.deceased OR g.bankruptcy OR g.open_dispute AS safety_flag,
               g.consent_phone, g.consent_sms, g.consent_email, g.consent_mail,
               coalesce(cases.language_of_service, crm.preferred_language) AS language_of_service,
               g.refresh_ts,
               -- extras
               g.consent_push, g.consent_autodialer,
               coalesce(crm.dncl, false) AS national_dncl, coalesce(crm.dnc, false) AS internal_do_not_call,
               coalesce(crm.third_party, false) OR coalesce(cases.third_party, false) AS third_party_representation,
               coalesce(crm.poa, false) AS power_of_attorney, coalesce(crm.legal_rep, false) AS legal_representative,
               coalesce(crm.counselling, false) AS credit_counselling, coalesce(crm.fraud_victim, false) AS fraud_victim,
               coalesce(crm.consumer_proposal, false) OR coalesce(cases.insolvency, false) AS insolvency,
               crm.voicemail_ok AS voicemail_permitted, crm.work_ok AS contact_at_work_permitted, crm.sunday_ok AS sunday_contact_permitted,
               crm.call_start AS permitted_call_start_local, crm.call_end AS permitted_call_end_local
        FROM gold_stage.c360_customer g LEFT JOIN crm USING (golden_customer_id) LEFT JOIN cases USING (golden_customer_id)
        LEFT JOIN contacts USING (golden_customer_id)""")
    return con.execute("SELECT count(*) FROM gold_stage.customer_safeguards").fetchone()[0]


DECISIONING_COLUMNS = [  # the allow-list. Adding a column here is a governance change: see tests/test_consumer_views.py
    "golden_customer_id", "segment", "province", "tenure_months", "employment_status", "occupation_category",
    "industry_naics2", "pay_frequency", "declared_annual_income", "income_verified_flag", "housing_status",
    "monthly_housing_cost", "product_count", "total_balance", "total_overdue", "max_dpd", "worst_bucket",
    "bureau_score", "bureau_score_model", "valid_from", "available_from",
]


def decisioning_customer(con) -> int:
    """History table: a row starts whenever a source changes for the person. Values are the latest AVAILABLE at that
    row's available_from, so a training join on available_from <= ptp_created_ts never sees the future.

    DEFAULTS:
      - quarter-end account snapshots and bureau pulls become available the next day at 06:00 (nightly batch);
        bureau files use file_received_ts when present
      - CRM keeps ONE version per record. Its attributes count only from that version's effective_from_ts; before it
        they are NULL, never back-filled with values that may not have been true yet
      - tenure is recomputed at each row's date, not copied from the extract
      - quarter-end snapshots have no overdue amount, so total_overdue is filled only on the current row
    """
    as_of = config.AS_OF_DATE.isoformat()
    con.execute(f"""CREATE OR REPLACE TEMP TABLE dc_crm AS
        SELECT g.golden_customer_id, c.effective_from_ts AS available_from, c.segment, c.province_code AS province,
               c.customer_since_date, c.employment_status, c.occupation_category, c.industry_naics2, c.pay_frequency,
               c.declared_annual_income, c.income_verified_flag, c.housing_status, c.monthly_housing_cost
        FROM gold_stage.c360_customer g JOIN curated.customers_current c ON c.crm_customer_id = g.survivor_crm_customer_id""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE dc_snap AS
        SELECT a.golden_customer_id, s.month_end_date::TIMESTAMP + INTERVAL 30 HOUR AS available_from,
               s.month_end_date::TIMESTAMP + INTERVAL 1 DAY AS valid_from,
               count(DISTINCT s.account_id) FILTER (WHERE s.account_status <> 'closed')::INTEGER AS product_count,
               sum(s.balance) FILTER (WHERE s.product_type IN {CREDIT_PRODUCTS}) AS total_balance,
               max(s.dpd)::INTEGER AS max_dpd, arg_max(s.dpd_bucket, {bucket_rank('s.dpd_bucket')}) AS worst_bucket
        FROM curated.account_monthly_snapshot s JOIN gold_stage.c360_account a USING (account_id)
        WHERE s.month_end_date <= DATE '{as_of}'
        GROUP BY 1, 2, 3""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE dc_bureau AS
        SELECT x.golden_customer_id, available_from, any_value(valid_from) valid_from,
               arg_max(risk_score, valid_from) bureau_score, arg_max(risk_score_model, valid_from) bureau_score_model
        FROM (SELECT bank_subject_ref, pull_date::TIMESTAMP valid_from, pull_date::TIMESTAMP + INTERVAL 30 HOUR available_from,
                     risk_score, risk_score_model FROM curated.bureau_history WHERE risk_score IS NOT NULL
              UNION ALL
              SELECT bank_subject_ref, pull_date::TIMESTAMP, coalesce(file_received_ts, pull_date::TIMESTAMP + INTERVAL 30 HOUR),
                     risk_score, risk_score_model FROM curated.external WHERE risk_score IS NOT NULL) b
        JOIN gold_stage.customer_xref x ON x.node_id = 'bureau|' || b.bank_subject_ref
        WHERE b.valid_from <= TIMESTAMP '{as_of}'
        GROUP BY 1, 2""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE dc_current AS
        SELECT golden_customer_id, TIMESTAMP '{as_of}' + INTERVAL 30 HOUR AS available_from, TIMESTAMP '{as_of}' AS valid_from,
               product_count, total_balance, total_overdue, max_dpd, worst_bucket
        FROM gold_stage.c360_customer""")
    con.execute("""CREATE OR REPLACE TEMP TABLE dc_points AS
        SELECT golden_customer_id, available_from, max(valid_from) valid_from FROM (
            SELECT golden_customer_id, available_from, available_from valid_from FROM dc_crm WHERE available_from IS NOT NULL
            UNION ALL SELECT golden_customer_id, available_from, valid_from FROM dc_snap
            UNION ALL SELECT golden_customer_id, available_from, valid_from FROM dc_bureau
            UNION ALL SELECT golden_customer_id, available_from, valid_from FROM dc_current)
        GROUP BY 1, 2""")
    # account roll-ups: the current row comes from today's account tables, earlier rows from quarter-end snapshots
    con.execute("""CREATE OR REPLACE TEMP TABLE dc_accounts AS
        SELECT golden_customer_id, available_from, product_count, total_balance, NULL::DOUBLE AS total_overdue, max_dpd, worst_bucket FROM dc_snap
        UNION ALL SELECT golden_customer_id, available_from, product_count, total_balance, total_overdue, max_dpd, worst_bucket FROM dc_current""")
    con.execute(f"""CREATE OR REPLACE TABLE gold_stage.decisioning_customer AS
        SELECT p.golden_customer_id, c.segment, c.province,
               CASE WHEN c.customer_since_date IS NOT NULL THEN date_diff('month', c.customer_since_date, p.valid_from)::INTEGER END AS tenure_months,
               c.employment_status, c.occupation_category, c.industry_naics2, c.pay_frequency, c.declared_annual_income,
               c.income_verified_flag, c.housing_status, c.monthly_housing_cost,
               a.product_count, a.total_balance, a.total_overdue, a.max_dpd, a.worst_bucket,
               b.bureau_score::DOUBLE AS bureau_score, b.bureau_score_model,
               p.valid_from, p.available_from
        FROM dc_points p
        ASOF LEFT JOIN dc_crm c ON c.golden_customer_id = p.golden_customer_id AND p.available_from >= c.available_from
        ASOF LEFT JOIN dc_accounts a ON a.golden_customer_id = p.golden_customer_id AND p.available_from >= a.available_from
        ASOF LEFT JOIN dc_bureau b ON b.golden_customer_id = p.golden_customer_id AND p.available_from >= b.available_from""")
    cols = [r[0] for r in con.execute("DESCRIBE gold_stage.decisioning_customer").fetchall()]
    if cols != DECISIONING_COLUMNS:
        raise ValueError(f"decisioning_customer columns drifted from the allow-list: {cols}")
    return con.execute("SELECT count(*) FROM gold_stage.decisioning_customer").fetchone()[0]


def customer_timeline(con) -> int:
    """Only events on or before AS_OF_DATE; only eligible, masked text (curated.*_text from text_controls)."""
    end = _as_of_end()
    con.execute(f"""CREATE OR REPLACE TABLE gold_stage.customer_timeline AS
        SELECT {_owner('h')} AS golden_customer_id, h.contact_ts_local AS event_ts, 'contact' AS event_type,
               'contact_history' AS source_table, h.contact_id AS source_id, h.account_id, h.agent_id,
               h.channel_resolved AS channel, NULL::VARCHAR AS text, h.voice_sample_id,
               h.case_id, concat_ws(' | ', h.direction, h.outcome_desc,
                   CASE WHEN h.payment_taken_flag THEN 'payment ' || h.payment_amount END,
                   CASE WHEN h.ptp_made_flag THEN 'promise made' END,
                   CASE WHEN h.hardship_mentioned_flag THEN 'hardship mentioned' END,
                   CASE WHEN h.dispute_raised_flag THEN 'dispute raised' END,
                   CASE WHEN h.cease_request_flag THEN 'cease request' END) AS summary
        FROM curated.contact_history h {_owner_joins('h')}
        WHERE h.contact_ts_local <= {end}
        UNION ALL
        SELECT {_owner('p')}, p.ptp_created_ts, 'promise', 'promises_to_pay', p.ptp_id, p.account_id, p.agent_id, p.ptp_channel, NULL, NULL,
               p.case_id, 'promised ' || p.ptp_amount || ' by ' || p.ptp_due_date ||
                   CASE WHEN p.status_ts <= {end} THEN ' | ' || p.ptp_status ELSE '' END
        FROM curated.promises_to_pay p {_owner_joins('p')}
        WHERE p.ptp_created_ts <= {end}
        UNION ALL
        SELECT {_owner('n')}, n.note_ts, 'note', 'agent_notes', n.note_id, n.account_id, n.agent_id, NULL, t.text_masked, NULL,
               n.case_id, n.note_type
        FROM curated.agent_notes n JOIN curated.agent_notes_text t USING (note_id) {_owner_joins('n')}
        WHERE t.eligible AND n.note_ts <= {end}
        UNION ALL
        SELECT {_owner('c')}, c.call_start_ts, 'transcript', 'call_transcripts', c.transcript_id, NULL, c.agent_id, 'call', t.text_masked,
               c.voice_sample_id, c.case_id, c.direction || ' call, ' || c.duration_sec || ' s, ' || c.language
        FROM curated.call_transcripts c JOIN curated.call_transcripts_text t USING (transcript_id) {_owner_joins('c')}
        WHERE t.eligible AND c.call_start_ts <= {end}
        UNION ALL
        SELECT cc.golden_customer_id, o.offer_ts, 'offer', 'offers', o.offer_id, o.account_id, o.agent_id, NULL, NULL, NULL,
               o.case_id, concat_ws(' | ', o.program_id, o.eligibility_result, o.customer_response, o.plan_status)
        FROM curated.offers o LEFT JOIN gold_stage.case_customer cc USING (case_id)
        WHERE o.offer_ts <= {end}
        UNION ALL
        SELECT a.golden_customer_id, t.txn_ts, 'payment', 'transactions', t.txn_id, coalesce(t.target_account_id, t.account_id), NULL,
               t.channel, NULL, NULL, NULL, t.txn_type || ' ' || abs(t.amount) || ' ' || coalesce(t.currency, '')
        FROM curated.transactions t JOIN gold_stage.c360_account a ON a.account_id = t.account_id
        WHERE t.txn_type IN ('card_payment', 'loan_payment') AND t.txn_ts <= {end}""")
    con.execute("DELETE FROM gold_stage.customer_timeline WHERE golden_customer_id IS NULL")
    return con.execute("SELECT count(*) FROM gold_stage.customer_timeline").fetchone()[0]


def bucket_transitions(con) -> int:
    # DEFAULT grain: consecutive quarter-end snapshots of the same account (the snapshot table is quarterly).
    con.execute(f"""CREATE OR REPLACE TABLE gold_stage.bucket_transitions AS
        SELECT account_id, golden_customer_id, product, period_start, period_end, bucket_start, bucket_end,
               dpd_start::INTEGER AS dpd_start, dpd_end::INTEGER AS dpd_end,
               status_start, status_end,
               {bucket_rank('bucket_end')} > {bucket_rank('bucket_start')} AS rolled_forward,
               {bucket_rank('bucket_end')} < {bucket_rank('bucket_start')} AS rolled_back
        FROM (SELECT s.account_id, a.golden_customer_id, s.product_type AS product,
                     lag(s.month_end_date) OVER w AS period_start, s.month_end_date AS period_end,
                     lag(s.dpd_bucket) OVER w AS bucket_start, s.dpd_bucket AS bucket_end,
                     lag(s.dpd) OVER w AS dpd_start, s.dpd AS dpd_end,
                     lag(s.account_status) OVER w AS status_start, s.account_status AS status_end
              FROM curated.account_monthly_snapshot s LEFT JOIN gold_stage.c360_account a USING (account_id)
              WINDOW w AS (PARTITION BY s.account_id ORDER BY s.month_end_date))
        WHERE period_start IS NOT NULL""")
    return con.execute("SELECT count(*) FROM gold_stage.bucket_transitions").fetchone()[0]


BUILDERS = [promises_enriched, customer_safeguards, decisioning_customer, customer_timeline, bucket_transitions]


def build(con) -> dict:
    out = {}
    for fn in BUILDERS:
        t0 = time.time()
        out[fn.__name__] = fn(con)
        print(f"  {fn.__name__:22s} {out[fn.__name__]:>12,} rows  {time.time() - t0:5.0f}s", flush=True)
    return out


def main() -> None:
    con = connect()
    print(build(con))
    con.close()


if __name__ == "__main__":
    main()
