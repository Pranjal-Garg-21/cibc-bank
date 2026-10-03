-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}100.0 * count(*) FILTER (WHERE dpd >= 1) / nullif(count(*),0) AS metric_value,
 count(*) FILTER (WHERE dpd >= 1) AS numerator, count(*) AS denominator
FROM gold.semantic_accounts WHERE snapshot_date=$snapshot_date AND account_status IN ('active','blocked')
 AND dpd IS NOT NULL AND ($product IS NULL OR product=$product)
{group_by}
