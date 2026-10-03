-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}100.0 * count(*) FILTER (WHERE cure_date >= case_open_date AND cure_date <= case_open_date + INTERVAL 30 DAY) / nullif(count(*),0) AS metric_value,
 count(*) FILTER (WHERE cure_date >= case_open_date AND cure_date <= case_open_date + INTERVAL 30 DAY) AS numerator,
 count(*) AS denominator
FROM gold.semantic_cases WHERE case_open_date >= $start AND case_open_date < $end
 AND case_open_date + INTERVAL 30 DAY < $observation_end
 AND NOT coalesce(insolvency_hold_flag,false) AND NOT coalesce(deceased_hold_flag,false)
{group_by}
