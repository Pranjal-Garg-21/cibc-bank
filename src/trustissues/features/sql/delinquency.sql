-- Arrears from account snapshots strictly before the promise date. The snapshots are QUARTERLY
-- (quarter-ends 2023-12-31 to 2026-06-30), so 12 months = up to 4 snapshots. Account level when the promise
-- names an account with snapshots; otherwise the customer's accounts rolled up per month (worst dpd).
WITH acct AS (
    SELECT s.ptp_id, a.month_end_date, a.dpd
    FROM spine s
    JOIN src_account_snapshots a ON a.account_id = s.account_id
    WHERE a.month_end_date < CAST(s.event_ts AS DATE)
      AND a.month_end_date >= CAST(s.event_ts AS DATE) - INTERVAL 12 MONTH
),
cust AS (
    SELECT s.ptp_id, a.month_end_date, max(a.dpd) AS dpd
    FROM spine s
    JOIN src_account_snapshots a ON a.crm_customer_id = s.crm_customer_id
    WHERE s.ptp_id NOT IN (SELECT ptp_id FROM acct)
      AND a.month_end_date < CAST(s.event_ts AS DATE)
      AND a.month_end_date >= CAST(s.event_ts AS DATE) - INTERVAL 12 MONTH
    GROUP BY s.ptp_id, a.month_end_date
),
months AS (
    SELECT *, 'account' AS level FROM acct
    UNION ALL
    SELECT *, 'customer' AS level FROM cust
)
SELECT
    s.ptp_id,
    arg_max(m.dpd, m.month_end_date) AS dpd_last_snapshot,
    max(m.dpd) AS worst_dpd_12m,
    count(*) FILTER (WHERE m.dpd > 0) AS snapshots_delinquent_12m,
    count(m.month_end_date) AS snapshots_observed_12m,
    date_diff('day', max(m.month_end_date), CAST(any_value(s.event_ts) AS DATE)) AS days_since_snapshot,
    CAST(any_value(m.level) = 'account' AS INTEGER) AS snapshot_account_level
FROM spine s
LEFT JOIN months m USING (ptp_id)
GROUP BY s.ptp_id
