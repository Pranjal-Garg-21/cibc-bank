# Codex P2 modules: integration handoff

The metric renderer, validator and executor are now wired into Claude's Ask service. Development uses in-memory or explicitly isolated dev databases; full-data validation and certification remain pending. See [the consolidated P2 implementation documentation](p2_documentation.md) for both authors' contributions, current integration status, validation, and remaining work. The worker warehouse must not be opened while loading/curation holds it.

## Claude-facing API

1. `semantic.SCHEMAS` is the explicit proposed table/column/type allow-list for metric views. `create_semantic_fixtures(con)` creates empty tables in memory. Reviewed interface fixtures remain in `common/fixtures.py`.
2. `metric_templates.render_metric(metric_id, parameters=..., dimensions=(...), cap_at=None, variant='default')` returns `MetricQuery` (`sql`, `parameters`, `metric_id`, `variant`). All 15 catalogue metrics have templates. There is also `cure_rate` variant `cohort_30d`. `docs/semantic_views.md` documents required parameters. Invalid dimensions, variants and unexpected/missing scalar parameters fail explicitly.
3. `validator.validate(sql, QueryPolicy(...))` returns `ValidatedQuery`. Required policy fields are the trusted role and approved table schemas. Pass authorised case/customer/account scopes from access code as `row_scopes={table: RowScope(column, tuple_of_authorised_ids)}`. Every table scanned by an agent requires a scope, including scans in CTEs/subqueries. An empty scope reads no rows; an omitted scope refuses. Never accept this policy from an LLM or user request.
4. `QueryPolicy.required_filters` adds trusted mandatory SQL predicates inside every underlying table scan; outer OR expressions cannot weaken them. Supply certified defaults here when validating generated SQL; the hand-written metric templates already contain their fixed defaults. Bind all predicate values through parameters. `QueryPolicy.join_keys` accepts approved `(table,column,table,column)` equality edges. `schema_graph.SEMANTIC_JOIN_KEYS` covers current metric joins. No inferred source relationship becomes authorised automatically.
5. `execute.execute(validated_query, scalar_parameters, con=in_memory_con)` is the fixture path. The later production path is `database=path`, opened read-only. Never pass the worker path while workers are active. Output is `QueryResult(columns, rows, elapsed_ms)`. Default runtime limit is 5 seconds, policy row cap defaults to 500 (maximum 10,000). Scope parameters use reserved `__ti_scope_` names that callers cannot override. **Since 4 Oct (Ask page):** an injected *file* connection is accepted too (the app holds the warehouse for writing, and DuckDB refuses a second connection to the same file in one process): the query runs on its own cursor inside `BEGIN TRANSACTION READ ONLY`, always rolled back, so DuckDB itself refuses any write and the caller's connection and open transaction are untouched (`tests/test_execute.py`).
6. `SchemaGraph.from_dictionary(path, primary_keys=...)` reads Relationships metadata. `join_path(tables)` proposes an approximate Steiner tree with fan-out penalties. Unknown multiplicity stays unapproved. Crosswalk edges require the source-system discriminator and are excluded from `approved_keys()` until explicitly handled. A path is metadata, not executable SQL.

Role policy: agents see explicitly scoped rows; team leads receive aggregate queries; analysts receive approved non-personal data. Entity IDs may be used internally for joins and COUNT but are not projected for analysts/leads. The validator excludes all sensitivity levels other than `none`, known renamed PII fields and configured proxies. Unknown columns are not in the schema allow-list. Date/identity display for customer screens belongs to access/masking and RAG, not this structured query interface.

Only SELECT trees are supported. Set operations, recursion, external readers/table functions, unknown functions, writes and unapproved joins refuse. This intentionally conservative subset can expand with tests when a real service requirement appears. Dataclasses are trusted internal handoff objects, not an API security boundary: don't construct `ValidatedQuery` directly from user SQL.

## Semantic adapters

`build_dev_adapters(con, development_database=True)` requires an in-memory DB or file named `*_ask_dev.duckdb`. It checks grain before any view mutation and builds views transactionally. It does not rebuild curated tables, install stubs or publish certified products. Inputs: eight curated tables listed in the function plus P1's crosswalk. No customer key is fabricated; unresolved identity stays NULL. Approval by P1/Claude of the contract is still needed.

The release lacks a loan account status field. The adapter marks charged-off loans as `charged_off` and all others as `unknown`; it does not silently call every loan active. The card delinquency variant is supported; active-loan eligibility needs a reviewed rule. Accounts currently include cards and loans, while snapshots retain all products. Payroll is one row per golden customer with maximum delay on eligible payroll accounts.

Historical case/account state cannot be rebuilt from their current snapshot: Claude's service must reject unsupported historical variants. Aggregated promise payment amounts may not reconstruct historical partial payments. Default cure is calendar-period cure; `cohort_30d` is separately named. Certification-at-assignment pilot comparison needs a reviewed additional semantic shape. Roll-rate hold exclusions are explicit because the dev reference and catalogue differ. All real-data reference comparisons are pending.

## Quality integration for P1

**Superseded at the P2 merge (4 Oct).** `run_checks`, `check_report.py` and `ops_anomaly.py` (P2's anomaly helper) were removed: the eight checks repeated P1's contract rules Q1-Q9 one for one, and the two they added were folded into `contracts/c360_customer.yaml` (overdue totals in Q7, build age as Q10). P1's `quality/anomaly.py` is the one anomaly detector. One publish gate: `contract_checks.run()`. See `merge.md` §6. The text below records P2's design.

`contract_checks.run_checks(con, as_of_date=..., checked_at=..., source_records=..., source_safety=..., decisioning_allowlist=...)` implements the final design's eight checks. Rules 1–5 block, 6–8 warn. Missing source inventory, safety evidence or approved decisioning allow-list yields `not_checked`; `publish_allowed(results)` fails closed on any incomplete blocking check. Call before publishing a staged product. Current table names follow gold stubs; adapting the staging schema is a P1 integration step.

`source_records` is the complete `(system,source_customer_key)` inventory, not keys derived from the crosswalk itself. `source_safety` is the independently derived per-golden-ID OR of cease/deceased/bankruptcy/dispute flags from sources. Without independent inputs, checks would merely compare a product to itself. `checked_at` is the real batch clock for 24-hour freshness; the dataset as-of date governs DOB/age validation. Customer totals compare the latest account snapshot and tolerate one cent.

P1's `contracts/c360_customer.yaml` is still absent. `load_contract(path)` reads it when supplied, but schema/rule-field binding awaits that contract. This implementation uses explicit DuckDB/Python checks, not a Great Expectations suite; agreement on YAML-driven GE integration or this implementation deviation remains open. It must not be represented as completed contract-YAML integration.

`anomaly.detect` consumes current/historical `Observation` and `OpsEvent` objects. It needs at least three prior observations, uses sample standard deviation and handles constant baselines. `manifest_counts` reads the latest manifest; the manifest is overwritten and cannot itself provide history. Use explicit `record_observation`/`load_history` batch hooks to preserve observations. Null rates are fractions per column and must be supplied by the batch. Overlapping incidents label supporting evidence, not causal proof. `check_report.render_report` (renamed from dq_report to avoid P1's file on main) returns Markdown; writing a report is an explicit caller action.

## Shared-file requirements (Claude is editor)

- Dependencies: sqlglot, networkx; pytest for development. They are installed locally in `.venv`; dependency metadata remains Claude's file.
- Dependency declarations and `metric_sql/*.sql` package-data configuration are now present in `pyproject.toml`. Claude remains its single editor; installed-wheel behavior has not been separately verified here.
- The service/catalogue/parameter adapters should use these signatures or agree a change before either side edits shared files.
- Do not activate worker-DB adapters or production execution until the warehouse is declared free.

## Validation

Dedicated tests cover all 15 metric calculations, cohort cure, partial/open/future promises, null DPD denominators, zero-contact cases, grouping allow-lists, PII through aliases/CTEs, trusted row scopes in nested scans, mandatory predicates, row caps, runtime interruption, read-only file execution, graph multiplicity, adapter grain, eight quality checks and anomaly baselines. Adapter schema tests use the committed dictionary, not downloaded data.
