-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}100.0 * count(*) FILTER (WHERE chargeoff_date >= $start AND chargeoff_date < $end) / nullif(count(*),0) AS metric_value,
 count(*) FILTER (WHERE chargeoff_date >= $start AND chargeoff_date < $end) AS charged_off_accounts,
 coalesce(sum(chargeoff_amount) FILTER (WHERE chargeoff_date >= $start AND chargeoff_date < $end),0) AS charged_off_amount,
 count(*) AS denominator
FROM gold.semantic_accounts WHERE snapshot_date=$snapshot_date
{group_by}
