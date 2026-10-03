# P2 plan v2: Ask (structured path)

Branch `p2-branch-ask-layer`. v2 folds in `p2_plan_review.md` (Codex) and Codex's confirmation of the split. v1 was commit `833714f`. Source documents: `implementation_plan.md` §4, `p2_design_sections.md`, `interfaces.md`, and the release's benchmark files and `metric_definitions.csv`. Nothing is built yet. Status of agreements is in §10.

## 1. What P2 is scored on

The graded output is `submission/benchmark_answers.csv` (`question_id, answer, sql_or_sources, refused`). 35 questions are released (21 dev with gold, 14 test hidden), plus more at **19:00 IST on 4 Oct**. So the system must generalise. The README allows small CSV tables or `key: value` entries inside `answer`; the outer file stays the four-column CSV.

I classified the 35 for planning only. The Ask pipeline itself must infer intent from the question text and must not read the `metric_id` column or gold answers; those are used only in development and scoring.

| Route | Questions | Owner |
|---|---|---|
| Certified metric (15 rows, **12 distinct metrics**; the catalogue has 15 metrics, all implemented) | 003 004 007 008 011 012 013 014 015 017 025 027 028 032 034 | P2 |
| Structured, no certified metric | 006 010 030 035 | P2 |
| Refusal | 018 023 024 029 031 | P2 |
| Policy / corpus RAG | 001 002 005 009 019 020 026 033 | P4 via I6 |
| Customer RAG (call on a case) | 016 021 022 | P4 via I6 |

## 2. Trap handling (corrected after review; verified against the dev reference SQL)

| Q | What the reference actually does / the trap | Handling |
|---|---|---|
| 003 | `least(prior_broken_ptp_count, 3)` groups 3+ together | approved grouping transformation (`cap_at`), not a question special-case |
| 007 | `LEFT JOIN agents`, `coalesce(site,'self_serve')`; due dates through 28 Sep; resolved statuses only | left join, keep unmatched group |
| 008 | July opening cohort, cure within 30 days, needs full follow-up | cohort variant of `cure_rate`, not the generic monthly denominator |
| 010 | uses supplied `within_permitted_hours_flag` and `contact_ts_local`; no extra timezone conversion | use the flag. (v1 wrongly proposed province-based logic.) |
| 011, 027 | `coalesce(channel_v2, channel)`; 027 also excludes system events | one approved adapter does the coalesce; P1 has not implemented it |
| 012 | includes `active` and `blocked`; null DPD excluded from the denominator, never treated as 0; `cycles_delinquent` is not days | metric variant, documented |
| 014 | randomised hardship cases, certification at assignment, 30-day cure, group sizes, direction of effect | needs `case_assignment_history` (inspect its grain); `assignment_method = 'random'`; no causal overreach |
| 015 | count and amount charged off by product, not a percentage | define which amount field is the loss |
| 017 | `month_end_date` parsed with three formats; both March and June DPD observed; keep history | confirm how failed `TRY_CAST` dates are recovered from raw text; account history, not current-only; check catalogue vs reference on charge-offs |
| 025 | do open cases with zero attempts belong in the denominator? | left join from cases; state the choice |
| 028 | `assignment_method = 'random'` only | explicit filter |
| 032 | certified rate stays on the structured case flag; text-inferred hardship may be a **separately labelled estimate** | never replaces the numerator |
| 034 | exclude open and cancelled from the denominator | certified `default_filters` |
| 035 | reference counts the **stored** `current_bucket`; trap note says recompute from `current_dpd` | **decision:** primary answer = stored-bucket counts (matches gold); then report the *measured* mismatch with DPD-derived buckets and the population used. No hard-coded "4%". Keep reported and canonical bucket as separate fields |
| 006, 030 | spike explained by incident (`ops_events`) | structured count plus cited incident evidence; no causal claim from coincidence |
| 024 | FSA is a planted proxy | refusal gate |
| 002, 026 | superseded documents | P4 (version in force) |

## 3. Query semantics (new)

- **Requested measure is separate from metric identity.** Parameter contract: `metric_id`, requested measure, cohort, date basis, grouping, comparison periods, approved filters. Each metric supports a bounded set of variants; an unsupported variant returns "cannot answer", never a nearby formula. Results include numerator/denominator or group counts.
- **Dates:** half-open `[start, end)` intervals, with the as-of-date interpretation documented per date basis (snapshot, contact, promise due, payment, case-open cohort). The references differ (007 includes 28 Sep; 011 stops before it; 006 uses 15–22 vs 7–14), so each metric records which it uses. Historical answers use only data known by the observation cutoff; metrics that cannot be rebuilt from current-only data are flagged.
- **Grain and joins:** preserve account, case, contact and promise grains separately; pre-aggregate one-to-many tables before joining so a customer/account join never multiplies promise denominators.
- **Namespace:** metric SQL reads `gold.semantic_*` only. P2 owns the semantic SQL and agrees column/grain contracts with P1. Dev adapters over curated data live only in an isolated dev database and are never published as certified products before the quality gate. No separate `semantic` schema.

## 4. SQL and parameter controls

One read-only `SELECT`; allowed tables and columns; join edges validated; role scope applied; bounded rows and runtime. Resolve aliases, CTEs and subqueries when checking columns; reject file/network readers and table functions; test scope removal inside subqueries. Scalar values are bound; grouping columns, sort choices and identifiers go through explicit allow-lists. Certified `default_filters` stay structural in templates. Generated SQL gets one repair attempt, then an explicit cannot-answer. Unclassified source columns are unavailable even if the refusal lexicon does not mention them.

## 5. One entry point

`ask(question, user, *, as_of_date)` serves the app and the benchmark. The benchmark passes each row's `as_of_date` and a stated benchmark user (role and id); no bypass. Access check, basic injection screening (heuristic fallback first) and the minimal validator are in the first runnable path. A fake RAG handler is wired early so P4 can integrate.

## 6. Ownership (agreed with Codex's confirmation)

| Codex | Claude |
|---|---|
| Metric SQL and tests | Metric catalogue and parameter extraction |
| `gold.semantic_*` views and dev adapters | Access checks |
| SQL validator and execution | `ask()` service, `AnswerCard`, benchmark harness and scorer |
| Schema graph | Dates (`asof`), refusal gate, injection screening, router |
| Contract checks, anomalies, DQ report | Generated-SQL fallback and drivers |
| Review of Claude's work | `masking.py` and P2's three UI pages (Team Lead, Identity Review, Data Quality) |

**Single editors:** Claude edits service wiring, shared config and dependency files (`pyproject.toml`, `settings.yaml`, `common/interfaces.py`). Codex gives requirements for its modules. Each reviews the other's work. P4 keeps `llm.py`, audit and RAG handlers. Any new file gets one implementer before it is touched.

## 7. Build order and delivery checkpoints

Clock times assume the plan's start of 3 Oct 09:00 IST; hours are from the implementation plan.

| Step | Deliverable | Needed by |
|---|---|---|
| 1 | Interfaces merged into the P2 branch (see §10); freeze `AnswerCard`, user context, `ask()` signature, semantic view contracts; populated fixtures | before any code |
| 2 | Claude: service/harness/scorer, dates, refusal gate, router, injection fallback, access. Codex: minimal validator, fixture-tested metric SQL | first runnable path |
| 3 | Case-to-call contract settled with P4 (case id → golden customer → latest eligible call *for that case*; assignment access check) | early, while fixtures suffice |
| 4 | Semantic adapters and metric variants; real tables brought in incrementally | hours 4–20 |
| 5 | **Contract checks (Codex) ready for P1's publish gate** | hour ~20 (4 Oct 05:00) |
| 6 | **`masking.py` (Claude) before any screen shows customer data** | before the first customer-data page |
| 7 | Fake→real RAG handler; generated-SQL fallback after access and graph checks; incident explanations and comparisons | hours 12–28 |
| 8 | Anomalies, DQ report (Codex); drivers, three UI pages (Claude) | hours 20–32 |
| 9 | Full-data benchmark, evidence review, unseen-question rerun | hour 34 (4 Oct 19:00) onward; final CSV by 21:00 |

Full-warehouse availability blocks full-data validation only, not fixture-based development.

## 8. Testing and the scorer

Unit tests: dates, refusal gate (the 5 refusals; "average" and "ageing buckets" allowed), validator (writes, PII, missing default filters, scope removal in subqueries), router paraphrases and hybrid questions (a case ID alone does not imply customer RAG), a fake LLM provider so tests need no credentials.

`scripts/score_dev.py` is a development approximation, not the official grader: compare table rows by dimension keys (not order); percentages in points, amounts in CAD, counts exact where required; report missing/extra rows, duplicates, empty denominators, refusal flags, cited sources; report text and citation review separately; unsupported tolerance formats are reported, never defaulted to pass; validate submitted IDs against the input and keep one row per question.

## 9. Risks

Curated is slow and unshared; the dev reference dates and the catalogue disagree in places; the extras at 19:00 will contain the same trap families; LLM cost/latency for the final run (cache via P4's wrapper); a sensitivity file generated locally from the dictionary must exist before real-data execution (unit work can use a small classified fixture).

## 10. Agreement status

| Item | Status |
|---|---|
| Ownership table (§6) | Codex confirmed; waiting for the human's OK |
| BQ-035 primary answer = stored bucket, mismatch measured and reported | agreed by both |
| Merge `interface-stubs-review` into the P2 branch (not `main`) | Codex recommends; **waiting for the human's go**; then review the diff and run the interface tests before coding |
| Case-to-call contract with P4 | open |
| `bucket`/`channel_v2` adapter location | P2 adapter for now; tell P1 |
| LLM provider and model ID | open (P4's wrapper) |
