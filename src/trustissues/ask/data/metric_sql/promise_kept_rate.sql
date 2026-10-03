-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}100.0 * count(*) FILTER (WHERE ptp_status='kept') / nullif(count(*),0) AS metric_value,
 count(*) FILTER (WHERE ptp_status='kept') AS numerator, count(*) AS denominator
FROM gold.semantic_promises
WHERE ptp_due_date >= $start AND ptp_due_date < $end AND ptp_status IN ('kept','partially_kept','broken') AND status_ts < $observation_end
 AND (NOT $random_only OR assignment_method='random')
{group_by}
