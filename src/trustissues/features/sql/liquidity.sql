-- Room to pay, at the latest (quarterly) snapshot strictly before the promise date, at most 4 months old. Account level when the
-- promise names an account with snapshots, otherwise the customer's credit accounts summed.
WITH acct AS (
    SELECT s.row_id, a.month_end_date, a.balance, a.limit_or_original_amount AS credit_limit, a.minimum_due,
           a.payment_made, CAST(a.overlimit_flag AS INTEGER) AS overlimit
    FROM spine s
    JOIN src_account_snapshots a ON a.account_id = s.account_id
    WHERE a.month_end_date < CAST(s.event_ts AS DATE)
      AND a.month_end_date >= CAST(s.event_ts AS DATE) - INTERVAL 4 MONTH
),
cust AS (
    SELECT s.row_id, a.month_end_date, sum(a.balance) AS balance, sum(a.limit_or_original_amount) AS credit_limit,
           sum(a.minimum_due) AS minimum_due, sum(a.payment_made) AS payment_made,
           max(CAST(a.overlimit_flag AS INTEGER)) AS overlimit
    FROM spine s
    JOIN src_account_snapshots a ON a.crm_customer_id = s.crm_customer_id
    WHERE s.row_id NOT IN (SELECT row_id FROM acct)
      AND a.limit_or_original_amount IS NOT NULL  -- credit products only
      AND a.month_end_date < CAST(s.event_ts AS DATE)
      AND a.month_end_date >= CAST(s.event_ts AS DATE) - INTERVAL 4 MONTH
    GROUP BY s.row_id, a.month_end_date
),
latest AS (
    SELECT * FROM (SELECT * FROM acct UNION ALL SELECT * FROM cust)
    QUALIFY row_number() OVER (PARTITION BY row_id ORDER BY month_end_date DESC) = 1
),
card_months AS (
    SELECT s.row_id, a.month_end_date,
           sum(a.balance) / nullif(sum(a.limit_or_original_amount), 0) AS utilisation
    FROM spine s
    JOIN src_account_snapshots a ON a.crm_customer_id = s.crm_customer_id
    WHERE a.product_type = 'card'
      AND a.month_end_date < CAST(s.event_ts AS DATE)
      AND a.month_end_date >= CAST(s.event_ts AS DATE) - INTERVAL 4 MONTH
    GROUP BY s.row_id, a.month_end_date
),
cards AS (
    SELECT row_id, arg_max(utilisation, month_end_date) AS card_utilisation FROM card_months GROUP BY row_id
)
SELECT
    s.row_id,
    c.card_utilisation,
    l.balance / nullif(l.credit_limit, 0) AS balance_to_limit,
    l.minimum_due / nullif(s.ptp_amount, 0) AS min_due_to_ptp_amount,
    l.payment_made AS payment_made_last_snapshot,
    CAST(l.payment_made >= l.minimum_due AS INTEGER) AS paid_min_last_snapshot,
    l.overlimit AS overlimit_last_snapshot
FROM spine s
LEFT JOIN latest l USING (row_id)
LEFT JOIN cards c USING (row_id)
