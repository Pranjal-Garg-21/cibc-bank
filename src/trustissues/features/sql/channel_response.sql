-- How the customer responded to contact in the 30 days strictly before the promise was made.
-- (The contact in which the promise was made has contact_ts = ptp_created_ts, so it is excluded.)
WITH c AS (
    SELECT s.row_id, s.event_ts, h.*
    FROM spine s
    JOIN src_contacts h ON h.golden_customer_id = s.golden_customer_id
    WHERE h.contact_ts < s.event_ts
      AND h.contact_ts >= s.event_ts - INTERVAL 30 DAY
)
SELECT
    s.row_id,
    count(c.contact_id) AS contacts_30d,
    count(c.contact_id) FILTER (WHERE c.channel = 'call') AS calls_30d,
    avg(CAST(c.rpc_flag AS INTEGER)) FILTER (WHERE c.channel = 'call') AS call_rpc_rate_30d,
    avg(CAST(c.ptp_made_flag AS INTEGER)) AS ptp_made_rate_30d,
    avg(CAST(c.email_opened_flag AS INTEGER)) FILTER (WHERE c.channel = 'email') AS email_open_rate_30d,
    avg(CAST(c.link_clicked_flag AS INTEGER)) FILTER (WHERE c.channel = 'sms') AS sms_click_rate_30d,
    avg(CAST(c.push_opened_flag AS INTEGER)) FILTER (WHERE c.channel = 'push') AS push_open_rate_30d,
    count(c.contact_id) FILTER (WHERE c.payment_taken_flag) AS payments_taken_30d,
    date_diff('day', max(c.contact_ts), any_value(s.event_ts)) AS days_since_last_contact
FROM spine s
LEFT JOIN c USING (row_id)
GROUP BY s.row_id
