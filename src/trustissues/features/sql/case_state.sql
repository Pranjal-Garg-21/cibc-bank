-- The collections case as it stood at the event time (case-level model only).
-- Arrears on day k of the case (dpd_at_day_k) are used only once known: k days after opening, strictly
-- before the event time. Promises and contacts count only if they happened before the event time; an
-- "open" promise was made before it and not resolved before it.
WITH c AS (
    SELECT s.row_id, s.event_ts, h.*,
           date_diff('day', h.case_open_ts, s.event_ts) AS age
    FROM spine s
    JOIN src_case_history h ON h.case_id = s.case_id
),
now AS (
    SELECT row_id,
           coalesce(CASE WHEN age > 90 THEN dpd_at_day_90 END, CASE WHEN age > 60 THEN dpd_at_day_60 END,
                    CASE WHEN age > 45 THEN dpd_at_day_45 END, CASE WHEN age > 30 THEN dpd_at_day_30 END,
                    CASE WHEN age > 21 THEN dpd_at_day_21 END, CASE WHEN age > 14 THEN dpd_at_day_14 END,
                    CASE WHEN age > 7 THEN dpd_at_day_7 END, entry_dpd) AS dpd_now,
           coalesce(CASE WHEN age > 90 THEN overdue_at_day_90 END, CASE WHEN age > 60 THEN overdue_at_day_60 END,
                    CASE WHEN age > 45 THEN overdue_at_day_45 END, CASE WHEN age > 30 THEN overdue_at_day_30 END,
                    CASE WHEN age > 21 THEN overdue_at_day_21 END, CASE WHEN age > 14 THEN overdue_at_day_14 END,
                    CASE WHEN age > 7 THEN overdue_at_day_7 END, entry_overdue) AS overdue_now
    FROM c
),
promises AS (
    SELECT s.row_id,
           count(p.ptp_id) AS case_promises,
           count(p.ptp_id) FILTER (WHERE p.ptp_status IN ('broken', 'partially_kept') AND p.status_ts < s.event_ts)
               AS case_promises_broken,
           count(p.ptp_id) FILTER (WHERE p.ptp_status = 'kept' AND p.status_ts < s.event_ts) AS case_promises_kept,
           arg_max(p.ptp_to_overdue_ratio, p.ptp_created_ts)
               FILTER (WHERE p.status_ts IS NULL OR p.status_ts >= s.event_ts) AS open_ptp_to_overdue_ratio,
           arg_max(date_diff('day', CAST(s.event_ts AS DATE), p.ptp_due_date), p.ptp_created_ts)
               FILTER (WHERE p.status_ts IS NULL OR p.status_ts >= s.event_ts) AS days_to_open_ptp_due
    FROM spine s
    JOIN src_promises p ON p.case_id = s.case_id AND p.ptp_created_ts < s.event_ts
    GROUP BY s.row_id
),
contacts AS (
    SELECT s.row_id,
           count(h.contact_id) AS case_contacts,
           count(h.contact_id) FILTER (WHERE h.rpc_flag) AS case_rpc,
           count(h.contact_id) FILTER (WHERE h.payment_taken_flag) AS case_payments_taken,
           date_diff('day', max(h.contact_ts), any_value(s.event_ts)) AS days_since_case_contact
    FROM spine s
    JOIN src_contacts h ON h.case_id = s.case_id AND h.contact_ts < s.event_ts
    GROUP BY s.row_id
)
SELECT
    s.row_id,
    c.age AS case_age_days,
    c.primary_product,
    c.case_open_reason,
    c.entry_dpd,
    c.entry_overdue,
    c.accounts_in_case_count,
    c.prior_cases_12m,
    n.dpd_now,
    n.overdue_now,
    n.dpd_now - c.entry_dpd AS dpd_change_since_open,
    n.overdue_now / nullif(c.entry_overdue, 0) AS overdue_vs_entry,
    coalesce(p.case_promises, 0) AS case_promises,
    coalesce(p.case_promises_broken, 0) AS case_promises_broken,
    coalesce(p.case_promises_kept, 0) AS case_promises_kept,
    CAST(p.open_ptp_to_overdue_ratio IS NOT NULL AS INTEGER) AS has_open_ptp,
    p.open_ptp_to_overdue_ratio,
    p.days_to_open_ptp_due,
    coalesce(k.case_contacts, 0) AS case_contacts,
    coalesce(k.case_rpc, 0) AS case_rpc,
    coalesce(k.case_payments_taken, 0) AS case_payments_taken,
    k.days_since_case_contact
FROM spine s
LEFT JOIN c USING (row_id)
LEFT JOIN now n USING (row_id)
LEFT JOIN promises p USING (row_id)
LEFT JOIN contacts k USING (row_id)
