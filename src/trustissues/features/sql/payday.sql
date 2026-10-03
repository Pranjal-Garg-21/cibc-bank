-- INTERIM payday features (before identity): salary credits on the customer's chequing snapshots
-- (quarterly, so 12 months = up to 4), and whether the customer stated an income date.
-- The stated income date always equals ptp_due_date when present (119,668 of 119,668), so the
-- due-date-vs-income-date gap carries no information; only the flag is kept.
-- Replaced by deposit-based salary_credit_history features once deposits link to golden IDs.
WITH sal AS (
    SELECT s.row_id, a.month_end_date, sum(a.salary_credit_amount) AS salary,
           max(a.month_end_date) OVER (PARTITION BY s.row_id) AS last_month
    FROM spine s
    JOIN src_account_snapshots a ON a.golden_customer_id = s.golden_customer_id
    WHERE a.salary_credit_amount IS NOT NULL
      AND a.month_end_date < CAST(s.event_ts AS DATE)
      AND a.month_end_date >= CAST(s.event_ts AS DATE) - INTERVAL 12 MONTH
    GROUP BY s.row_id, a.month_end_date
),
agg AS (
    SELECT row_id,
           count(*) FILTER (WHERE salary > 0) AS salary_snapshots_12m,
           arg_max(salary, month_end_date) AS salary_last,
           avg(salary) FILTER (WHERE month_end_date < last_month AND salary > 0) AS salary_prior_avg
    FROM sal GROUP BY row_id
)
SELECT
    s.row_id,
    coalesce(a.salary_snapshots_12m, 0) AS salary_snapshots_12m,
    a.salary_last,
    (a.salary_last - a.salary_prior_avg) / nullif(a.salary_prior_avg, 0) AS salary_change_pct,
    s.ptp_amount / nullif(a.salary_last, 0) AS ptp_amount_to_salary,
    CAST(s.customer_stated_income_date IS NOT NULL AS INTEGER) AS stated_income_date_flag
FROM spine s
LEFT JOIN agg a USING (row_id)
