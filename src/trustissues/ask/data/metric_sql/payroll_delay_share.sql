-- P2 reviewed-template draft; full-data certification pending.
SELECT 100.0 * count(*) FILTER (WHERE payroll_delay_days > 7) / nullif(count(*),0) AS metric_value,
 count(*) FILTER (WHERE payroll_delay_days > 7) AS numerator,count(*) AS denominator
FROM gold.semantic_payroll WHERE snapshot_date=$snapshot_date AND payroll_deposit_flag IS TRUE
