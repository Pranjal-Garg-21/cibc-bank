-- Two explicit, disjoint half-open periods. Labels are trusted scalar bindings.
SELECT CASE WHEN contact_ts_local >= $start AND contact_ts_local < $end
 THEN $period_label ELSE $comparison_label END AS period, count(*) AS disputes
FROM gold.semantic_contacts
WHERE dispute_raised_flag AND ((contact_ts_local >= $start AND contact_ts_local < $end)
 OR (contact_ts_local >= $comparison_start AND contact_ts_local < $comparison_end))
GROUP BY 1
ORDER BY 1
