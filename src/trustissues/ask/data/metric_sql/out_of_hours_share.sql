-- BQ-010 reference: lower bound only; caller supplies the release/as-of start.
-- semantic_contacts.channel already coalesces channel_v2 and channel.
SELECT {group_select}round(100.0 * avg(CASE WHEN NOT h.within_permitted_hours_flag THEN 1 ELSE 0 END),2) AS out_of_hours_pct,
 count(*) AS calls
FROM gold.semantic_contacts h INNER JOIN gold.semantic_cases c ON h.case_id=c.case_id
WHERE h.channel='call' AND h.direction='outbound' AND h.contact_ts_local >= $start
 AND c.province IS NOT NULL
{group_by}
ORDER BY 1
