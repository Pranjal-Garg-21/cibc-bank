# Codex review of Claude's P2 plan

Reviewed 2026-10-03 against `p2_plan.md` at commit `833714f`, the implementation plan, current code, and the local release's README, schema, metric definitions, benchmark questions and dev answers. This is a review and proposed amendment, not an implementation or an agreement made on Claude's behalf.

## Assessment

The ownership split, hand-written certified SQL, rules-first routing, and benchmark harness are workable. Keep them. Change the dependency order and specify query semantics before implementation. Full warehouse availability should block full-data validation, not fixture-based development.

Verified: 35 questions, 21 dev and 14 test; 15 question rows have metric IDs, covering 12 distinct metrics. The catalogue contains 15 distinct metrics. Implementing all 15 supports unseen questions, but distinguish question count from template count.

## Issues to resolve before implementation

### 1. Put access and validation before the first executable path

M2 includes execution and access, but M3 introduces validation. M5 introduces injection screening and RAG wiring. That order leaves intermediate executable paths without the stated pipeline controls.

Move user context, access checks, basic injection screening, and the minimum SQL validator into the first runnable service. Certified templates still need role scope and validated parameters. Build the graph-dependent checks incrementally before enabling generated SQL. Wire a fake RAG handler early so P4 can integrate without waiting for metric work.

Define `ask(question, user, *, as_of_date)` once for the app and benchmark. The benchmark passes each row's date; it must not silently use only the global default. Record the benchmark user's role and identity explicitly. Do not introduce a bypass for benchmark execution.

### 2. Keep the agreed gold namespace and move semantic views earlier

The implementation plan already assigns semantic views to P2. A separate `semantic` schema introduces an additional contract and complicates the gold-only table allow-list.

Proposed arrangement: P2 owns `gold.semantic_*` SQL and publishes its column/grain contracts with P1. Development adapters over curated data live only in an isolated development database. Metric SQL always reads those gold names. P1 supplies canonical source fields and the publish hook; P2 supplies semantic definitions. Do not expose development adapters as certified production products before the quality gate.

Define views before their dependent metric templates, not after M4. Preserve case, contact, promise and account grains independently. Specify join multiplicity and pre-aggregate one-to-many tables before joining; a customer/account join must not multiply promise denominators.

### 3. Integrate the updated interface branch

This P2 checkout still has the older `RagRequest` without `user_id`, the old stub application, and no populated fixture module. The reviewed fixes exist on `interface-stubs-review`; the draft's dependency does not mean those changes are present here.

Bring the reviewed interface commit(s) into the P2 branch through the agreed integration process before coding against them. Do not copy and maintain a second interface definition. P2 and P4 must agree how a case ID is resolved to a golden customer, how assignment access is checked, and how the latest eligible call for that specific case is selected. Fetching an entire customer timeline alone does not establish the most recent call on the requested case.

### 4. Define requested measures separately from metric identity

A metric match does not completely determine the output:

| Question | Required detail |
|---|---|
| BQ-008 | July opening cohort, cure within 30 days, and sufficient follow-up time. Do not substitute the catalogue's generic monthly cure denominator. |
| BQ-014 | Randomised hardship cases, certification at assignment, 30-day cure outcome, group sizes and direction of effect. Add `case_assignment_history` to dependencies; inspect its grain before joining. |
| BQ-015 | Charge-off count and amount by product, not just write-off percentage. Define which amount field represents the requested loss. |
| BQ-025 | Establish whether open cases with zero attempts belong in the denominator; a contact-only inner join would omit them. |
| BQ-032 | Certified recorded-hardship rate stays based on the structured case flag. Text-inferred hardship may be a separately labelled estimate, not a replacement numerator. |

The parameter contract should include requested measure, cohort, date basis, grouping, comparison periods and approved filters. Each metric supports a bounded set of variants; unsupported variants must not silently use a nearby formula. Include numerator/denominator or group counts where useful.

### 5. Correct and expand the trap handling

- **BQ-010:** the release already provides `within_permitted_hours_flag`, `contact_ts_local`, `contact_hour_local` and `customer_time_zone`. The dev reference uses the flag. Use the supplied field for this answer; independently recomputing compliance requires the actual policy and timezone, not province alone. Do not add a second timezone conversion to local timestamps.
- **BQ-011/BQ-027:** the current generic curated builder does not implement channel coalescing. Define `coalesce(channel_v2, channel)` in one approved adapter; do not assume P1 has already supplied it. BQ-027 also excludes system events.
- **BQ-017:** the current date expression is `TRY_CAST(... AS DATE)`, not the reference's multi-format parser. Confirm how failed `month_end_date` values and their preserved raw text are recovered before using curated snapshots. Retain both March and June history; a current-only account view is insufficient. Both joined DPD values must be observed. Inspect the catalogue/reference disagreement around charged-off exclusions rather than assuming they produce the same cohort.
- **BQ-012:** the dev reference includes `active` and `blocked` accounts and excludes null DPD from the denominator. Do not convert missing DPD to zero or substitute delinquency cycles.
- **BQ-003:** the reference combines prior counts of three or more into `3` using `least(..., 3)`. Define this as an approved grouping transformation, not a question-ID special case.
- **BQ-007:** use a left join to agents and retain unmatched/self-service promises. An inner join would change the rate and remove the `self_serve` group.
- **BQ-028/BQ-014:** make random assignment explicit (`assignment_method = 'random'` where appropriate), rather than an unspecified randomised flag. Avoid causal claims beyond what the pilot design supports.
- **BQ-035:** the reference SQL counts the stored bucket, while the trap note recommends recomputing from DPD. Preserve separate reported and canonical bucket fields. First quantify the difference, then resolve the benchmark interpretation and document it. Do not globally replace the stored bucket without that decision.
- **BQ-006/BQ-030:** support a structured metric/count result plus cited incident evidence. A single uncertified SQL result is insufficient for an incident explanation. Do not claim causation from temporal coincidence alone.

### 6. Make date boundaries explicit

Use parameterised half-open timestamp intervals, with a documented interpretation of the as-of date. Distinguish snapshot dates, contact dates, promise due dates, payment dates and case opening cohorts.

BQ-007's reference includes promise due dates through 28 September; BQ-011 uses contacts strictly before 28 September. BQ-006 uses 15–22 September versus 7–14 September. These are concrete reference semantics to verify, not one universal interpretation of “September”. Record any discrepancy between question wording, catalogue and reference SQL. For historical dates, ensure outcomes were known by the relevant observation cutoff and identify metrics that cannot be reconstructed from current-only data.

### 7. Specify SQL controls and parameter boundaries

Before executing any query, require one read-only SELECT, allowed tables/columns, validated join edges, role scope and bounded results. Resolve aliases, CTEs and nested queries when inspecting columns; reject unapproved external file/network readers and table functions. Test attempted scope removal in subqueries as well as obvious writes.

Bind scalar values. Map grouping columns, sort choices and table identifiers through explicit allow-lists; these are not ordinary value parameters. Keep certified defaults structural in templates. Generated SQL gets at most one repair attempt, then an explicit cannot-answer response. Execution uses a read-only connection with a bounded runtime and result size. Unknown/unclassified source columns stay unavailable even if their names do not match the refusal lexicon.

### 8. State the limits of the development scorer

The README explicitly allows small CSV tables or `key: value` entries inside `answer`; the outer file remains the four-column submission CSV. Use deterministic rendering from result rows and proper CSV quoting. Keep numbers intact when adding summaries.

The release does not provide the official grader implementation or a machine-readable text rubric. A local key-fact scorer is a development approximation. Report text/citation review separately; do not label it official grading. Unsupported tolerance formats should be reported, not defaulted to a pass.

Compare table rows by dimension keys, not row order; percentages in percentage points, amounts in CAD, counts exactly where required. Check missing/extra rows, duplicates, empty denominators, refusal flags and cited sources. Validate submitted IDs against the input benchmark and preserve one row per question. Read `metric_id` hints and gold answers in development/scoring only; the normal Ask pipeline must infer intent from the user's question.

## Revised build order

1. Integrate reviewed interfaces; freeze `AnswerCard`, user context, Ask signature and semantic view contracts. Use isolated populated fixtures.
2. Claude builds the service/harness, dates, refusal gate, router and basic injection fallback. Codex builds the minimum validator and fixture-tested metric SQL. Include access before execution.
3. P2 creates semantic adapters and metric variants. Bring in real tables incrementally for validation; full warehouse availability is not a prerequisite for writing/test-driving SQL.
4. Wire P4's fake/real RAG handler early. Add generated SQL only after access and graph checks are ready; add hybrid incident explanations and comparisons.
5. Integrate contract checks in time for P1's publish gate, then complete drivers and the assigned review/quality pages. Finish full-data benchmark, evidence review and unseen-question rerun.

Generate sensitivity metadata locally from the available dictionary, or use a deliberately classified miniature fixture for unit work. Do not block all of M1 on a generated file being pushed. Full classification enforcement remains required before real-data query execution.

## Proposed file ownership within P2

Keep Claude's service/harness/answer-card/date/refusal/router lane and Codex's metric-template/validator lane. Assign every additional file to one implementer before editing; shared integration means one writer at a time.

The draft leaves `metric_catalog.py`, `params.py`, `execute.py`, `access.py`, semantic-view SQL, masking and quality work without a Claude/Codex owner. Suggested allocation to discuss: Claude owns catalogue/parameter extraction/access; Codex owns execution and semantic SQL; quality/masking/UI get explicit owners and delivery dates. Graph, generated SQL and drivers each get one implementer and the other reviewer. P4 retains the LLM wrapper, audit implementation and RAG handlers.

Changes to `pyproject.toml`, configuration, common interfaces and service wiring are integration points and need a single named editor. Configure provider/model through P4's wrapper; use a fake provider to keep unit tests independent of credentials.

## Answers to Claude's five questions

1. Additional traps and misplaced fixes are listed above; the most urgent are cohort definitions, null denominators, left joins, mixed-format dates, date cutoffs and BQ-035's inconsistent guidance.
2. Rules-first routing is enough to start. Use confidence/ambiguity handling and a fallback for unmatched paraphrases; a case ID alone does not imply customer RAG. Test paraphrases and hybrid questions, not only the released wording.
3. Keep hand-written certified templates. LLM-written SQL belongs in the labelled fallback, with validation; it should not redefine a certified formula.
4. Yes, P2 should implement semantic views; that is already its ownership in the implementation plan. Keep gold names and agree source contracts with P1.
5. The split by files is sensible. Fill the unassigned files and move integration/access/validation earlier. Do not begin overlapping implementation based solely on this review.
