-- Certification pilot: 30-day cure after a randomised_pilot assignment, certified vs not certified agents.
-- Unit = one pilot assignment (the randomised event); certification and hardship as recorded at assignment time.
SELECT CASE WHEN certified_for_vulnerable_flag THEN 'certified' ELSE 'not_certified' END AS agent_group,
 100.0 * count(*) FILTER (WHERE cure_date >= CAST(assigned_ts AS DATE) AND cure_date <= CAST(assigned_ts AS DATE) + INTERVAL 30 DAY) / nullif(count(*),0) AS metric_value,
 count(*) FILTER (WHERE cure_date >= CAST(assigned_ts AS DATE) AND cure_date <= CAST(assigned_ts AS DATE) + INTERVAL 30 DAY) AS numerator,
 count(*) AS denominator
FROM gold.semantic_assignments WHERE assignment_method = 'randomised_pilot'
 AND certified_for_vulnerable_flag IS NOT NULL
 AND (NOT $hardship_only OR hardship_flag_at_assignment)
 AND CAST(assigned_ts AS DATE) + INTERVAL 30 DAY < $observation_end
 AND NOT coalesce(insolvency_hold_flag,false) AND NOT coalesce(deceased_hold_flag,false)
GROUP BY 1
