# P2 plan: Ask (structured path) — DRAFT for review

Branch `p2-branch-ask-layer`. Written by Claude for Codex to challenge and improve; both of us then agree before building. Nothing here is built yet. Source documents: `implementation_plan.md` §4, `p2_design_sections.md`, `interfaces.md`, and the release's `benchmark_questions.csv`, `benchmark_dev_answers.csv`, `metric_definitions.csv`.

## 1. What P2 is scored on

The graded output is `submission/benchmark_answers.csv` (`question_id, answer, sql_or_sources, refused`), produced by running `ask()` on every benchmark question. 35 questions are released (21 `dev` with gold answers, 14 `test` hidden), plus more released at **19:00 IST on 4 Oct**. So the system must generalise, not memorise the 35.

I classified all 35 by what they need:

| Route | Count | Question IDs | Owner of the answer |
|---|---|---|---|
| Certified metric (SQL) | 15 | 003 004 007 008 011 012 013 014 015 017 025 027 028 032 034 | P2 |
| Structured, no certified metric (uncertified SQL) | 4 | 006 010 030 035 | P2 |
| Refusal (guardrail) | 5 | 018 023 024 029 031 | P2 |
| Policy / corpus RAG | 8 | 001 002 005 009 019 020 026 033 | P4 via I6 |
| Customer RAG (call/notes) | 3 | 016 021 022 | P4 via I6 |

P2 therefore owns 24 of 35 directly and routes 11 to P4. Metric answers have numeric **tolerances** (e.g. ±0.5 pts, ±0.1 days, exact, ±1 CAD), so correctness is checkable against the dev gold.

## 2. Traps hiding in the questions (these decide the score)

Each is a deliberate data or reasoning trap. The plan has to put the fix in a known place.

| Q | Trap | Fix lives in |
|---|---|---|
| 011 | `channel` moved to `channel_v2` from 26 Sep; filtering on `channel` alone drops late-September rows | P1 curated (coalesce the two) + P2 must not query raw columns |
| 012 | `dpd` missing for ~8% of accounts; `cycles_delinquent` counts cycles, not days | metric SQL (documented handling) |
| 017 | `month_end_date` in several formats; `dpd_bucket` inconsistent in ~4%; `dpd` missing ~8% | curated typing (P1) + metric SQL recomputes bucket from dpd |
| 035 | ~4% of rows have a bucket that disagrees with `current_dpd`; strong answer recomputes from dpd | gold/curated canonical bucket (P1) — **needs agreement** |
| 034 | exclude open and cancelled promises from the denominator | certified `default_filters` |
| 028 | only randomised cases give an unbiased comparison | metric SQL filter on randomised flag; note in answer |
| 032 | agent-coded hardship under-records (~60% recall); notes and transcripts show more | answer should state the caveat (and could use P4 text features) |
| 006, 030 | spikes explained by incidents (INC-0914, outage) in `ops_events` | uncertified SQL must also check `ops_events` and say so |
| 010 | out-of-hours calls concentrate in NL and SK (dialer on head-office time) | needs local-time logic by province; verify data |
| 024 | FSA is a planted proxy for income and protected grounds | refusal gate (not just PII/protected columns) |
| 002, 026 | superseded documents give the wrong answer | P4 (version in force) |

## 3. Pipeline and files (from the implementation plan, with order)

`access → injection → refusal gate → as-of dates → router → path → validator → answer card`. No AI in access, refusal, dates or validator.

### Milestones (in build order)

| M | Deliverable | Needs data? | Done when |
|---|---|---|---|
| M0 | `ask/answer_card.py`, `ask/service.py` stub, `scripts/run_benchmark.py` skeleton writing the CSV, `scripts/score_dev.py` | no | CSV written for all 35 with `not_found`; scorer runs on dev gold |
| M1 | `common/asof.py` (+tests), `ask/guards/refusal_gate.py` + `config/refusal_lexicon.yaml`, `ask/router.py` | no (needs `sensitivity.yaml`) | 5 refusal questions refused, no false refusals on the other 30; dates resolve from 2026-09-28 |
| M2 | `ask/data/metric_catalog.py`, `metric_sql/<metric_id>.sql` for the 15 certified metrics, `params.py`, `execute.py`, `access.py` | yes (curated) | dev metric questions within tolerance of gold |
| M3 | `ask/data/validator.py` (sqlglot), `schema_graph.py`, `schema_linker.py`, `uncertified_sql.py` | yes | 006 010 030 035 answered, labelled "uncertified" |
| M4 | `ask/data/drivers.py`, comparison handling | yes | 003 007 028 style questions |
| M5 | I6 wiring to P4 (`RagRequest/RagResponse`), `common/audit.py` use, guard `injection.py` | no | 11 RAG questions routed and returned |
| M6 | Hardening: re-run benchmark after every change, fix failures; 19:00 extras on 4 Oct | yes | final CSV |

Moved to P2 in the rebalance, scheduled after M4 unless P1 needs help sooner: `quality/contract_checks`, `anomaly`, `dq_report`, Team Lead / Identity Review / Data Quality pages, `masking.py`, and the `semantic_*` views.

## 4. Design choices to challenge

1. **Metric SQL is hand-written per certified metric**, with `default_filters` built in and named parameters. The LLM only fills filters/dates as JSON. Alternative: LLM writes SQL from the formula. I prefer hand-written for the 15 certified metrics because correctness is graded and the formulas are pseudo-SQL.
2. **Router: rules first, LLM only to break ties.** Keyword and metric-synonym match decides `metric`; `guardrail` comes from the refusal gate; the 11 RAG questions are recognisable (policy words, case IDs like `CS-2026-614370`). Is an LLM router needed at all?
3. **Where the "canonical bucket from dpd" lives.** If P2 recomputes it in each metric SQL, it is duplicated. Better: P1 provides one canonical `bucket` in curated/gold. Ask P1.
4. **Reading raw vs curated vs gold.** Plan says consumers read only `gold.*`. Gold isn't built; curated tables exist. Proposal: develop against `curated.*` through a thin set of views in a `semantic` schema with the same names we will later point at gold, so nothing in `metric_sql` changes when gold lands.
5. **Answer format in the CSV.** `answer` text must contain the numbers and a short statement; `sql_or_sources` holds the SQL or citation list; `refused` true/false. Does the grader parse tables in `answer`? Check the release README for the exact expected shape before M0.
6. **Refusal gate scope.** Beyond protected/PII columns: bulk personal-data export (023), ranking/targeting by protected attributes (029), FSA-style proxies (024), out-of-scope topics (018). Should the gate also catch "legal advice" (020 "Can we sue…")? That question has a gold-like policy answer, so probably route to RAG, not refuse. Needs a decision.
7. **LLM use in P2.** Only (a) filling metric parameters, (b) uncertified SQL, (c) two-sentence summary and follow-ups. Provider and model ID are not decided anywhere. Needs a decision (cost and rate limits matter for the 19:00 rerun).
8. **Latency target.** Plan says 1–2 s; P2's doc said 0.65–1.3 s. Not graded by the CSV, so low priority.

## 5. Dependencies on other owners

| Need | From | By |
|---|---|---|
| Interface stubs agreed (branch `interface-stubs-review`) | all | now |
| Curated tables for the metric sources: `collections_cases`, `promises_to_pay`, `contact_history`, `card_accounts`, `loan_accounts`, `account_monthly_snapshot`, `ops_events`, `agents` | P1 | status note says built or building |
| `config/sensitivity.yaml` generated and pushed | P1 | before M1 |
| Canonical bucket / `channel` coalescing decision | P1 | before M2 |
| I6 request/response including `user_id` | P4 | before M5 |
| Real data on my machine | a copy of Anushka's finished `trustissues.duckdb` (about 6 GB, verified with `verify_warehouse.py`), or a local raw load plus the needed curated tables | before M2 |

## 6. Test plan

- Unit: `test_asof`, `test_refusal_gate` (the 5 refusals plus "average" and "ageing buckets" allowed), `test_validator` (writes, PII columns, missing default filters rejected), router cases.
- Scorer `scripts/score_dev.py`: compares our CSV with `benchmark_dev_answers.csv` using each question's `tolerance`. Numeric within tolerance, text by key facts, refusals by flag. Reports per-question pass/fail and the trap note on failure.
- The benchmark is the integration test: run after every change.

## 7. Risks

- **Curated is slow and not shared.** Until I have data, M2 onwards is blocked. Mitigation: M0 and M1 first; get a copy of the finished warehouse.
- **Overfitting to the 21 dev answers.** The 19:00 extras are unseen. Keep metric SQL general over dimensions (product, queue, site, province, date window).
- **Data traps beyond the ones listed.** Expect more in the extras (the same families: schema drift, missing dpd, date formats, superseded documents).
- **LLM cost/latency** for the final run. Cache by input hash through `common/llm.py` (P4 ships it).

## 8. Proposed split between Claude and Codex (to agree)

| Lane | Items |
|---|---|
| Claude | M0 skeleton and scorer, `asof`, refusal gate and lexicon, router, answer card, service wiring |
| Codex | review of this plan; `metric_sql` templates and tests for the 15 metrics; `validator.py` with sqlglot tests; independent review of Claude's lane |
| Joint, one at a time | `schema_graph`, `uncertified_sql`, `drivers` (decide who builds, the other reviews) |

Rules: separate files, small commits, pull before work, nothing to `main` without the human's say-so.

## 9. Questions for Codex

1. Which of the 11 traps in §2 did I miss or misplace?
2. Is rules-first routing enough, or does it fail on the unseen extras?
3. Hand-written metric SQL vs LLM-written: any case for the second?
4. Should P2 build the `semantic` views itself rather than wait for P1's `gold.semantic_*`?
5. Is the Claude/Codex split sensible, or should we split by metric group instead?
