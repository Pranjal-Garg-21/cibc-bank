-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}100.0 * count(*) FILTER (WHERE b.dpd > $start_dpd_max) / nullif(count(*),0) AS metric_value,
 count(*) FILTER (WHERE b.dpd > $start_dpd_max) AS numerator,count(*) AS denominator
FROM gold.semantic_snapshots a JOIN gold.semantic_snapshots b ON a.account_id=b.account_id
WHERE a.snapshot_date=$start_snapshot AND b.snapshot_date=$end_snapshot
 AND a.dpd BETWEEN $start_dpd_min AND $start_dpd_max AND b.dpd IS NOT NULL
 AND (NOT $exclude_holds OR (a.account_status NOT IN ('charged_off','insolvency_hold')
                            AND b.account_status NOT IN ('charged_off','insolvency_hold')))
{group_by}
