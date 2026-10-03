-- The customer's (golden customer: all their CRM records and cases) earlier promises. A promise counts as broken/kept only once its status was known
-- (status_ts strictly before the event time); "made" counts use the creation time.
SELECT
    s.row_id,
    count(q.ptp_id) FILTER (WHERE q.ptp_status IN ('broken', 'partially_kept')
                              AND q.status_ts < s.event_ts
                              AND q.status_ts >= s.event_ts - INTERVAL 90 DAY) AS broken_ptp_90d,
    count(q.ptp_id) FILTER (WHERE q.ptp_created_ts >= s.event_ts - INTERVAL 90 DAY) AS ptp_made_90d,
    count(q.ptp_id) FILTER (WHERE q.ptp_status IN ('broken', 'partially_kept')
                              AND q.status_ts < s.event_ts
                              AND q.status_ts >= s.event_ts - INTERVAL 365 DAY) AS broken_ptp_365d,
    count(q.ptp_id) FILTER (WHERE q.ptp_status = 'kept'
                              AND q.status_ts < s.event_ts
                              AND q.status_ts >= s.event_ts - INTERVAL 365 DAY) AS kept_ptp_365d,
    count(q.ptp_id) FILTER (WHERE q.ptp_status IN ('kept', 'broken', 'partially_kept')
                              AND q.status_ts < s.event_ts) AS resolved_ptp_all,
    date_diff('day',
              max(q.status_ts) FILTER (WHERE q.ptp_status IN ('broken', 'partially_kept') AND q.status_ts < s.event_ts),
              s.event_ts) AS days_since_last_broken
FROM spine s
LEFT JOIN src_promises q
       ON q.golden_customer_id = s.golden_customer_id
      AND q.ptp_created_ts < s.event_ts
GROUP BY s.row_id, s.event_ts
