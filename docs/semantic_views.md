# P2 semantic view contracts (proposed for P1/Claude review)

> **Built in the warehouse since 4 Oct (P2 merge).** `factory/gold/views.py` `semantic_views()` builds all seven into `gold_stage` and the publish gate copies them to `gold` (contract rule Q11 checks the shapes below exactly). Same column definitions as `semantic_adapters.py`; the person on each row comes from P1's identity, not the CRM ID in the xref: cases from `gold.case_customer`, promises and contacts from their case's person (else their own CRM ID), accounts, snapshots and payroll from `gold.c360_account`. Rows: cases 367,229, promises 238,815, contacts 2,688,337, accounts 1,064,500, snapshots 12,569,692, payroll 579,994, assignments 423,830 (added 4 Oct for BQ-014). On the published warehouse every certified-metric answer in the benchmark is the same as on the dev database (`run_e.csv`). The dev adapter still works for a separate `*_ask_dev.duckdb` copy; a definition change must go into both. Run the benchmark on the warehouse: `python scripts/run_benchmark.py --database warehouse/trustissues.duckdb`.

These are fixture-tested contracts, not certified real-data products. Exact columns and DuckDB types live in `src/trustissues/ask/data/semantic.py`. P2 owns definitions; P1 supplies identity and publish-gate integration. No worker database is opened by fixture creation.

| View | Grain | Columns / purpose | Metrics |
|---|---|---|---|
| `gold.semantic_cases` | one row per case at the release snapshot | case/customer keys, opening date, product, queue, province, assigned agent, DPD, reported/canonical buckets, outcome, cure date, structured hardship, holds, random assignment, test cell, total overdue and days to cure (added 4 Oct for BQ-107/111/115) | cure, average DPD, hardship, attempts |
| `gold.semantic_promises` | one row per promise | promise/case/customer/account keys, product, site, queue, strategy cell, assignment method, creation/due/status/payment dates, prior broken count, status, amount paid, the channel the promise was taken on (`ptp_channel`, added 4 Oct for BQ-112) | kept/broken rate, collections |
| `gold.semantic_contacts` | one row per contact | contact/case/customer keys, canonical channel, direction, LOCAL timestamp/hour, RPC/PTP flags, cost, supplied permitted-hours flag, disputes, province/site, payment link sent and clicked (added 4 Oct for BQ-108) | RPC, PTP, contact cost, cost/RPC, attempts |
| `gold.semantic_accounts` | one row per (product, account, snapshot date) | account/customer keys, product/code, status, DPD, reported/canonical buckets, balance, overdue, charge-off date/amount | delinquency, write-off |
| `gold.semantic_snapshots` | one row per (account, snapshot date) across products; confirm account IDs globally unique | customer key, product/status, DPD, reported/canonical buckets; all history retained | roll rate |
| `gold.semantic_payroll` | one row per (customer, snapshot date) | customer key, payroll flag, maximum payroll delay across that customer's eligible chequing accounts | payroll delay share |
| `gold.semantic_assignments` | one row per case assignment (`case_assignment_history`) | assignment/case/agent keys, assigned and unassigned time, method (`randomised_pilot`, `rule_router`, `manual`), reason, certification and hardship **as recorded at assignment**, DPD at assignment; the case's opening date, cure date and holds carried on the row so no join is needed. **Template-only:** hidden from the SQL fallback's prompt and removed from its validator policy (`semantic.TEMPLATE_ONLY`), so that prompt and its cached replies are unchanged | certification pilot (BQ-014) |

Keys are join/scope fields, not personal details for display. The validator restricts non-agent output of entity IDs. All exposed columns require explicit classification approval; new source columns never expand these contracts automatically.

Adapters must preserve row grain: unique CRM crosswalk, case keys and agent IDs before joining. Unmatched site is `self_serve` via a LEFT JOIN. An unresolved customer remains NULL; never invent an identity or use a many-to-many crosswalk join. Channel is `coalesce(channel_v2, channel)`. Reported buckets remain as received; canonical buckets derive from observed DPD with the release ranges. Negative/missing DPD has no canonical bucket. Source dates use the current curated recovery logic, retaining historical snapshots.

The inspected release development build has exactly one retained row per account (1,064,500 rows and distinct account IDs). Closed/charged-off accounts can retain older snapshot dates. Write-off therefore includes all rows with `snapshot_date <=` the as-of date, producing an account denominator of 1,064,500 rather than 861,596 exact-date rows. If multiple historical rows per account are introduced into this view, select one eligible lifecycle row before calculating this metric; historical observations belong in `semantic_snapshots`. Current delinquency continues to require the exact snapshot date, active/blocked status and observed DPD. See [the write-off correction and validation](p2_write_off_fix.md).

Creation and outcome fields coexist in promises for reporting, not for model features. Dates and labels must be observed by the caller's explicit cutoff. Current case/account fields cannot reconstruct historical states; unsupported historical questions must be rejected by the service. Local contact timestamps are not converted again. The timestamp/date boundary convention is supplied by Claude's date resolver, not inferred from the metric ID.

## Template parameters

`render_metric(metric_id, dimensions=(), cap_at=None)` returns SQL with named scalar parameters. Identifiers and grouping transformations come only from the renderer's allow-list. Every template takes its required scalar date parameters explicitly. Percentage outputs are in percentage points (0–100). Empty denominators return NULL, not zero. Grouped results include population counts. Rates exclude unresolved future outcomes via `observation_end` where available.

| Metric | Required parameters | Fixed definition |
|---|---|---|
| `promise_kept_rate`, `broken_promise_rate` | `start`, `end`, `observation_end`, `random_only` | due date `[start,end)`, resolved statuses, known outcome; partial is denominator only |
| `rpc_rate` | `start`, `end` | outbound canonical-channel calls; true RPC / attempts |
| `ptp_rate` | `start`, `end` | true PTP among RPC contacts / RPC contacts |
| `cost_per_contact`, `cost_per_rpc` | `start`, `end` | exclude system events; summed contact cost / contacts or true RPCs |
| `collected_amount` | `start`, `end`, `observation_end` | amount paid against promises, payment date window; this variant is promise payments, not an estimate of all recoveries |
| `hardship_rate` | `start`, `end` | case-open cohort; STRUCTURED recorded flag, no text numerator substitution |
| `average_dpd` | none | open cases (`outcome IS NULL`), observed current DPD; release snapshot only |
| `delinquency_rate` | `snapshot_date` | active/blocked accounts, observed DPD; product-specific card variant supported |
| `write_off_rate` | `start`, `end`, `snapshot_date` | charge-off date `[start,end)` count / all account rows observed on or before `snapshot_date`; also emits charge-off count and amount; retains older closed/charged-off snapshots |
| `contact_attempts_per_case` | `start`, `end` | open cases LEFT JOIN outbound contacts; include zero-contact cases |
| `payroll_delay_share` | `snapshot_date` | customers with payroll; delay >7 / eligible customers |
| `roll_rate` | `start_snapshot`, `end_snapshot`, `start_dpd_min`, `start_dpd_max`, `exclude_holds` | paired observed DPD; worse canonical rank; catalogue exclusions optional and disclosed (benchmark reference differs) |
| `cure_rate` | `start`, `end`, `observation_end` | default: cases open at period start cured within period; `cohort_30d`: matured opening cohort cured within 30 days; both exclude insolvency/deceased holds |
| `cure_rate` variant `certification_pilot` | `hardship_only`, `observation_end`; no dimensions | one row per `randomised_pilot` assignment (the randomised event), optionally only cases flagged hardship at assignment; cured = cure date within 30 days after assignment; assignments under 30 days old and insolvency/deceased holds left out; grouped certified / not certified. The service adds the direction, the difference and a two-proportion z-test (`service.pilot_effect`) |

Two additional benchmark-specific templates are now available beyond the original 15 metrics:

| Template | Caller parameters / grouping | Definition |
|---|---|---|
| `out_of_hours_share` | `start`; requires `dimensions=('province',)` | outbound calls from the lower bound, INNER JOIN cases, non-NULL province, supplied permitted-hours flag, two-decimal percentage and count; no upper bound, matching dev reference |
| `dispute_count` | `start`, `end`, `comparison_start`, `comparison_end`; no dimensions | dispute flag in two disjoint half-open local-midnight periods; compact labels generated and bound by renderer |

See [Claude/P1 handoff](p2_claude_handoff.md) for wiring, review findings, and pending publication/contract decisions. These additions are fixture-tested; warehouse comparisons remain pending.

BQ-008's cohort variant and default calendar-period cure are implemented separately. BQ-014's certification pilot is the `certification_pilot` variant over `gold.semantic_assignments` (4 Oct): the unit is the pilot assignment, so no case is multiplied by a join. On 28 Sep: certified 4.06% (48 of 1,183), not certified 2.45% (33 of 1,349), +1.61 points, z = 2.30, p = 0.022. BQ-035 uses reported buckets as its primary answer; mismatch statistics are separate. These are explicitly remaining integration questions, not hidden changes to metric definitions.

## Ownership and integration

Codex owns `semantic.py`, `semantic_adapters.py`, `metric_templates.py`, SQL templates, validator, execution and schema graph. Claude owns catalogue, parameter extraction, access, dates and service. Pass an explicit validator policy and a validated query to execution; do not execute LLM text directly. Review the scope contract in `docs/p2_codex_modules.md`.

Development adapters require a database explicitly marked as a dev database by the caller and refuse the configured worker warehouse. They are built in a separate copy after curation completes; fixture work creates empty in-memory contract tables. Real-data results and certification are pending.
