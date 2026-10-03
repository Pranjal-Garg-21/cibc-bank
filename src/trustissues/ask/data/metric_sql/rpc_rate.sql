-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}100.0 * count(*) FILTER (WHERE rpc_flag) / nullif(count(*),0) AS metric_value,
 count(*) AS denominator
FROM gold.semantic_contacts
WHERE contact_ts_local >= $start AND contact_ts_local < $end AND direction='outbound' AND channel='call'
{group_by}
