-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}coalesce(sum(amount_paid_against),0) AS metric_value, count(*) AS promises
FROM gold.semantic_promises
WHERE paid_date >= $start AND paid_date < $end AND paid_date < CAST($observation_end AS DATE)
{group_by}
