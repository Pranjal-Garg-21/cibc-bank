-- P2 reviewed-template draft; full-data certification pending.
SELECT {group_select}avg(current_dpd) AS metric_value, count(*) AS cases, count(current_dpd) AS denominator
FROM gold.semantic_cases WHERE outcome IS NULL
{group_by}
