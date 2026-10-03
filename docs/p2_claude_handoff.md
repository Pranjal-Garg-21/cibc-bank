# P2 handoff to Claude and P1 — 3 October 2026

Status: the user asked to leave this handoff while P1 work proceeds. No warehouse publication, development rebuild, or contract binding was run. The worker warehouse remains closed. Changes below are local and uncommitted; catalogue, configuration, interfaces, and service wiring remain Claude's files.

## 1. Ready for Claude: deterministic BQ-006 and BQ-010 templates

Two additional reviewed-template paths are registered in `metric_templates.METRICS` and `FAMILIES`. These are benchmark-specific additions to the original 15 catalogue metrics, not a correction to the catalogue's count.

| Metric ID | Caller input | Output |
|---|---|---|
| `out_of_hours_share` | `parameters={'start': date_or_ISO_timestamp}`, `dimensions=('province',)` | `province, out_of_hours_pct, calls` |
| `dispute_count` | `parameters={'start': ..., 'end': ..., 'comparison_start': ..., 'comparison_end': ...}`, no dimensions | `period, disputes` |

### BQ-010

`metric_sql/out_of_hours_share.sql` uses semantic contacts' coalesced channel, outbound calls, a lower timestamp bound, and the supplied permitted-hours flag. It inner-joins cases on case ID and excludes NULL province. The percentage is rounded to two decimal places. NULL flags count in the denominator but not as violations, matching the reference's `CASE WHEN NOT flag THEN 1 ELSE 0 END`.

The lower-bound-only window deliberately matches the released development SQL and Claude's request. It has **no upper/as-of cutoff**. That is safe to interpret only for the fixed release containing no future rows. If used beyond that snapshot, agree an explicit upper-bound variant rather than claim the validator enforces as-of filtering. The output header is `province`, as requested, whereas gold uses `province_code`.

### BQ-006

`metric_sql/dispute_count.sql` counts `dispute_raised_flag` in two explicit disjoint half-open intervals. For the released question, pass:

```python
render_metric('dispute_count', parameters={
    'start': '2026-09-15', 'end': '2026-09-23',
    'comparison_start': '2026-09-07', 'comparison_end': '2026-09-15',
})
```

The renderer generates bound labels `15-22 Sep` and `7-14 Sep` itself, without leading zeros or caller-written SQL. Labels are not accepted as input parameters. Cross-month/year labels include the full boundary dates. Increasing windows, local midnight boundaries, and non-overlap are required. No contacts/flag filters beyond the requested dispute flag are added. Like the released grouped reference, an empty period does not emit a zero row.

Gold CSV confirms counts **875** and **566**. These were read from the reference artifact, not reproduced on warehouse data. Claude can now add triggers and parameter extraction for these IDs in `catalog.py`, including `days_before(period, 8)` for the preceding window. Update routing expectations for BQ-006/BQ-010 from SQL to metric in Claude's tests once wired.

## 2. Team-lead aggregate wrappers

The current validator checks aggregation at the outer SELECT and rejects a plain wrapper over an aggregated subquery. I have retained this deliberate conservative restriction for now; keep prompt rule 9 and the repair hint. Relaxation needs output-lineage analysis proving every outer source is aggregate-derived, safe grouping keys, no detail source joined back in, and no entity identifier exposure. Allowing any subquery merely because it contains a GROUP BY would be insufficient.

This is a usability limitation, not a claim that aggregate wrappers are inherently unsafe. It can be revisited with dedicated positive and adversarial tests after the urgent template integration.

## 3. Missing variants and existing behavior

| Question / issue | Current answer and next action |
|---|---|
| BQ-014: certification pilot | Not implemented. Assignment history contains `assignment_id`, `case_id`, `assigned_ts`, `assignment_method`, `certified_for_vulnerable_flag`, and `hardship_flag_at_assignment`. Define which assignment anchors each case and its 30-day clock before joining; otherwise repeated assignments multiply the population. Use certification at assignment, not an agent's present certification. Agree the randomisation predicate, hardship cohort, maturity cutoff, holds, and output groups with P1/Claude, then add an approved semantic shape and joins. No new shared schema has been silently added. |
| BQ-015: charge-off count/amount | Existing `write_off_rate` already emits `charged_off_accounts` and `charged_off_amount`, with grouping by `product` supported, alongside rate and denominator. No additional SQL is necessary to calculate those measures. Claude must select/display the requested measures rather than imply the rate is the requested answer. Scorer currently does not verify count and amount together. |
| BQ-025: zero attempts | Yes, open cases with zero outbound attempts are in the denominator. Existing template left-joins per-case attempt counts and coalesces absent attempts to zero. This prevents inflating the average by retaining only contacted cases. Group by stored `reported_bucket` for “current bucket”. |
| BQ-027: system events | Existing cost-per-contact and cost-per-RPC SQL excludes `channel='system'`. The semantic channel is `coalesce(channel_v2, channel)`. Confirm upstream canonical values remain lowercase; SQL does not invent a separate system-event classification. |
| BQ-013 and BQ-008 plausibility | Not run on development data: no development DB file was available under `warehouse/ask_dev` at inspection, and workers have not been released. Existing fixtures exercise payment-date amount and distinct matured 30-day cure. After release, run BQ-013/008 on the reviewed dev build and report populations, NULL coverage, maturity exclusions, rates/amounts, and date ranges; absence of gold is not certification. |
| Loan status | Adapter supports observed loan DPD, balance, overdue, charge-off dates/amounts, and charged-off count/amount. Non-charged-off status is `unknown`, so it cannot establish an active-loan delinquency denominator. Card active/blocked delinquency is supported. Do not label unknown loans active. |

## 4. P1 publication handoff — leave pending

P1 should own the real warehouse write and timing alongside its product publication. Codex owns semantic SQL definitions; Claude owns service wiring. `build_dev_adapters` intentionally refuses ordinary worker filenames and must not be bypassed or renamed into production.

Before a production publisher is added/run, P1 and P2 should agree:

1. Warehouse release and one named writer for the publication transaction.
2. Availability/types of eight curated inputs and the real `gold.customer_xref`; interface stubs are not populated identities.
3. Uniqueness/grain checks before all joins, crosswalk source-system/status rules, unresolved identity handling, sensitivity classification, and the supported populations in [semantic contracts](semantic_views.md).
4. A separate production publication entry point, preserving development-only guards, with transactional replacement and an explicit quality gate.
5. Post-publication schema/grain checks and reference comparisons, then application connections opened read-only.

No production publisher or gold views were installed by this handoff. The untracked dev builder reads the warehouse and rebuilds its output; only P1's agreed release permits running it.

## 5. P1 contract gate — target 4 October 05:00, timezone to confirm

`contracts/c360_customer.yaml` is still absent at inspection. The eight checks exist in `contract_checks.py`; rules 1–5 block and 6–8 warn. Missing independent source inventory, safety evidence, or decisioning allow-list fails closed. Do not fabricate a placeholder contract and call it integration.

P1 should provide the contract path/schema/version, staging table names, required fields, approved decisioning fields, and where pre-publish results are persisted. Agree whether the DuckDB/Python checks satisfy the contract or whether Great Expectations is a required deliverable. `load_contract` currently reads YAML but does not compile rules or create GE suites. Binding remains pending until that decision and file exist.

P1 must supply independent source inventory and safety evidence, the batch clock, and dataset as-of date. Wire `run_checks` before publication; block on incomplete blocking rules; use `check_report.write_report` with a separate agreed output path. Preserve Anushka's `dq_report.py` and canonical curated DQ report. Anomaly alerts currently do not influence the contract publish gate; that policy needs agreement. Persist batch observations because the overwritten manifest alone cannot supply history.

## 6. Review of Claude's Ask lane and fallback

The inspected service/access wiring denies agents portfolio metrics and generated SQL. Policy access allows all three roles; customer access allows agents only their assigned cases and team leads. Missing and inaccessible case wording is identical. Authenticated identity must be supplied by trusted application code: `UserContext` is not authentication. Customer retrieval and benchmark resolver wiring are still missing.

The fallback prompt correctly uses approved schema metadata, supplied hours flags, stored current buckets, half-open windows, top-level aggregates, no UNION/readers, and uncertified output. Generated SQL is validated and executed read-only, with one repair maximum. The prompt is guidance; validator policy remains the enforcement point. Findings for Claude:

- **As-of enforcement:** “No data exists after it” is an assumption, not a checked predicate. A schema containing later observations can yield future data. Add trusted path-specific temporal filters where the semantic/date basis permits, or explicitly restrict the path to the fixed release snapshot.
- **Provider errors:** initial generation and repair model calls can raise model/provider errors outside the SQL catch. Convert outages to an explicit cannot-answer, as the policy handler does.
- **Repair error disclosure:** binder/conversion exception text is sent back to the model. Conversion messages may contain offending values. Redact data-bearing error text before repair; metadata-only initial prompting does not prove the repair prompt contains no row data.
- **Measure selection:** catalogue matching can map unsupported pilot/historical/multi-measure wording to a nearby template. Explicitly reject unsupported shapes or render the reviewed variant; do not call a generic cure answer a certification-pilot result.
- **Output/audit:** metric SQL is the original parameterised template rather than the executed rewritten statement; bound parameters and complete citation metadata are not retained in the answer. Model-call auditing is not a full Ask audit. SQL fallback notes are not appended to benchmark answer text as metric notes are, although SQL cards carry `certified=False`.
- **Policy warehouse access:** `rag_handler` can trigger P4 hardship-table reads. A dev metric connection does not make `--llm` warehouse-free while workers hold the real database.

No changes were made to Claude's files in this review.

## 7. Validation and local files to integrate

The new templates and existing validator/executor suites passed **46 tests in 7.82 seconds**, using fixture/temporary databases only. New cases cover supplied false/true/NULL flags, unmatched cases/provinces, inbound/non-call/pre-window exclusion, two-decimal output, exact dispute boundaries and labels, overlap rejection, and required province grouping. Both templates pass the team-lead validator. Full-data counts remain pending.

The existing Ask gates/router/service/benchmark, working-tree RAG/fallback, and semantic adapter suites also passed **71 tests in 3.48 seconds** after these additions. Claude's catalogue wiring is still pending; these checks do not establish benchmark accuracy on real data.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B -m pytest \
  tests/test_metric_templates.py tests/test_validator.py tests/test_execute.py \
  -q -p no:cacheprovider
```

Codex's new code changes: `ask/data/metric_templates.py`, `ask/data/metric_sql/out_of_hours_share.sql`, `ask/data/metric_sql/dispute_count.sql`, and `tests/test_metric_templates.py`. Documentation is this handoff plus the semantic contract updates. Nothing was staged or committed; do not include other agents' in-progress files when integrating.
