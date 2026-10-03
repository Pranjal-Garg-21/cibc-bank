-- P2 reviewed-template draft; full-data certification pending.
WITH attempts AS (
 SELECT case_id,count(*) AS attempts FROM gold.semantic_contacts
 WHERE direction='outbound' AND contact_ts_local >= $start AND contact_ts_local < $end GROUP BY case_id
)
SELECT {group_select}sum(coalesce(a.attempts,0)) * 1.0 / nullif(count(*),0) AS metric_value,
 sum(coalesce(a.attempts,0)) AS numerator,count(*) AS denominator
FROM gold.semantic_cases c LEFT JOIN attempts a ON c.case_id=a.case_id WHERE c.outcome IS NULL
{group_by}
