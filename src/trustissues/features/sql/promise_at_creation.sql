-- Attributes of the promise as recorded when it was made (promises_to_pay). Known at ptp_created_ts.
-- prior_broken_ptp_count = earlier broken promises in the same case (checked: all resolved before creation).
SELECT
    s.row_id,
    p.dpd_at_creation,
    p.prior_broken_ptp_count,
    p.ptp_to_overdue_ratio,
    p.overdue_at_creation,
    p.ptp_amount,
    p.days_to_due_at_creation,
    p.ptp_sequence_in_case,
    p.ptp_type,
    p.ptp_channel,
    p.payment_method_promised,
    CAST(p.reminder_scheduled_flag AS INTEGER) AS reminder_scheduled_flag,
    p.grace_days,
    p.product
FROM spine s
JOIN src_promises p ON p.ptp_id = s.ptp_id
