-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}sum(contact_cost_cad) / nullif(count(*) FILTER (WHERE rpc_flag),0) AS metric_value,
 count(*) FILTER (WHERE rpc_flag) AS denominator
FROM gold.semantic_contacts
WHERE contact_ts_local >= $start AND contact_ts_local < $end AND channel <> 'system'
{group_by}
