-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}100.0 * count(*) FILTER (WHERE cure_date >= $start AND cure_date < $end) / nullif(count(*),0) AS metric_value,
 count(*) FILTER (WHERE cure_date >= $start AND cure_date < $end) AS numerator,count(*) AS denominator
FROM gold.semantic_cases WHERE case_open_date < $start AND (outcome_date IS NULL OR outcome_date >= $start)
 AND NOT coalesce(insolvency_hold_flag,false) AND NOT coalesce(deceased_hold_flag,false)
 AND $end <= CAST($observation_end AS DATE)
{group_by}
