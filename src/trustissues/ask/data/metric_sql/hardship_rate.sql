-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}100.0 * count(*) FILTER (WHERE hardship_flag) / nullif(count(*),0) AS metric_value,
 count(*) FILTER (WHERE hardship_flag) AS numerator, count(*) AS denominator
FROM gold.semantic_cases WHERE case_open_date >= $start AND case_open_date < $end
{group_by}
