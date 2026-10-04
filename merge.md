# Merge notes: things to reconcile when integrating everyone's work

Working list for the integration pass. Each item says what overlaps, which version to keep, and what to change. Tick items off as they are done.

---

## 1. `src/trustissues/factory/curate/text_controls.py`: built twice

The plan lists this file once, under P1. P1 (on `main`) and P4 (on `p4/policy-rag`, with `docs/text_controls.md` and `tests/test_text_controls.py`) each built it **at the same path**, so the merge will conflict on this file.

**Same in both:** notes marked `needs_review` and notes past `retention_until` are excluded; `none` and `auto_redacted` are allowed, with masking applied to everything; transcripts need both PII and PCI redaction, with retention from `contact_history.recording_retention_until`; card numbers are masked as any 13–19 digit run, not only numbers that pass a card-validity check. Both found the same problem: the redaction label is not reliable (card numbers in 108 `auto_redacted` notes).

| Point | P4 version | P1 version (`main`) |
|---|---|---|
| Form | SQL **views** `curated.note_text_eligibility`, `curated.transcript_text_eligibility`, plus a Python `scrub()` and `eligible_notes()` | Materialised **tables** `curated.agent_notes_text`, `curated.call_transcripts_text` with masked text, masking done in SQL |
| Empty notes | Excluded (`empty_text`) | Marked eligible (22,202 blank notes) |
| Missing retention date | Refused (`no_retention_date`) | Would be allowed (none in this data, so no effect today) |
| Dated after the as-of date | Excluded at the gate | Not filtered here (the timeline filters it) |
| Allowed statuses | Setting `text_controls.note_redaction_statuses` in `config/settings.yaml` | Hard-coded |
| Postal codes, other long digit runs (account numbers) | Masked (`[POSTAL]`, `[NUMBER]`) | Not masked. Checked: none survive in P1's output on this data |
| Dates of birth (`DOB 1996-04-19`) | **Not masked** | Masked (`[DOB]`) |
| Transcript text | **Not built**: eligibility only, turns not parsed or scrubbed | Parses the JSON turns into `agent: … / customer: …` text and masks it |
| What was replaced | Counts per kind (`{"CARD": 1}`) for the audit log | A yes/no `masked` flag |
| Tests | 26 | 6 masking cases (in `tests/test_consumer_views.py`) |
| Eligible notes | 688,046 | 710,248 (difference is mostly the blank notes) |
| Speed | Python scrub per note | SQL, about 1 s for 760k notes |

**Decision: keep P4's version as the single file, and add P1's two extras to it.**

To do (done 4 Oct in the P4 merge, branch `merge/p4-into-main`; details in `docs/text_controls.md` §4 and §6):
- [x] Take P4's `text_controls.py`, `docs/text_controls.md`, `tests/test_text_controls.py` on conflict.
- [x] Add DOB masking to P4's `scrub()`: a date written after `DOB`, `date of birth`, `DDN`, `né(e) le` becomes `[DOB]`. Tests added; business dates (`PTP $180 Jul 31`, `due 2026-10-01`) stay untouched.
- [x] Add transcript text to P4's version: P1's SQL parses `raw.transcript_json` turns into `speaker: text` lines in turn order, then `scrub()` runs on it.
- [x] Check P4's transcript view: it now takes one `contact_history` row per `contact_id`. 25,000 rows on the real data, and a test with a repeated `contact_id`.
- [x] `customer_timeline`: chose to materialise the scrubbed text once. `build_text_tables()` writes `curated.agent_notes_text` / `curated.call_transcripts_text` (same names and columns as P1's, plus `scrubbed`) from P4's eligibility views and `scrub()`, so `gold/views.py` did not change. 9 s for all notes and transcripts. SQL `regexp_replace` was not used: DuckDB's regex has no look-behind, so P4's patterns cannot be ported exactly.
- [x] `tests/test_consumer_views.py`: the `mask_sql` test is gone; its cases moved to `tests/test_text_controls.py` (against `scrub()`).
- [x] `pipeline.py`: unchanged; the `text_controls` stage calls `main()`, which now builds the views and the text tables.
- [x] Docs: `docs/consumer_views.md` rows G and H and `docs/documentation.md` D19 point at `docs/text_controls.md`.
- [x] Re-ran `text_controls`, `views`, `publish`. Timeline row counts: `docs/text_controls.md` §6.
- [ ] **Team decision still open:** whether `none` notes may be sent to an outside LLM at all (P4's setting allows it by default).

---

## 2. `src/trustissues/common/db.py` and `config/settings.yaml`: expected conflicts

Both are known to be changed on the P4 branch as well as on `main`.

- [x] `db.py`: keep **both** changes (merged without a conflict: P3 had already taken P4's cap byte for byte). `main` adds the `identity` and `gold_stage` schemas to `SCHEMAS`; P4 adds DuckDB resource caps (`memory_limit`, `threads`, `preserve_insertion_order`) in `connect()`.
- [x] `settings.yaml`: keep **both**. Done in the P4 merge. `main` adds keys under `identity:`; P4 adds `duckdb:`, `text_controls:`, `llm:` sections.

---

## 3. The identity LLM second opinion should go through `common/llm.py`

**On hold (4 Oct, P4 merge):** `common/llm.py` is now on `main`, but its default provider is OpenRouter's free Nemotron and identity pairs carry unmasked PII. Decided to leave `second_opinion.py` unchanged and not run it against any provider (Anthropic included) until the team agrees which provider may receive these pairs. Grey-zone pairs stay with human review (the safe default). Instead, the second opinion is being done by hand (agent labellers working only from the two records): `scripts/second_opinion_manual.py` exports the pairs and imports checked verdicts into `identity.llm_verdicts` without any API call (`docs/identity.md` §4 step 6).

`src/trustissues/factory/identity/second_opinion.py` (on `main`) calls the Anthropic SDK directly, because `common/llm.py` did not exist when it was written. The plan says every LLM call goes through `common/llm.py` (caching, prompt version, audit).

- [ ] Once `common/llm.py` is on `main`, change `second_opinion._ask()` to call it, and drop the `identity.llm_model` / `llm_effort` settings in favour of the shared `llm:` section.
- [ ] **Team decision:** identity pairs contain unmasked names, dates of birth, phone numbers and postal codes, because those are what the matcher compares. Agree which LLM provider may receive them before running it (3,343 pairs waiting).
- [ ] Keep the safe default: if the call fails or no key is configured, pairs go to the human review queue and records stay separate.
- [ ] Same for `src/trustissues/factory/curate/mapping_assistant.py`: it calls OpenRouter (Nemotron 3 Super) itself through `_post()` with its own `mapping_assistant:` section in `config/settings.yaml`. Replace `_post()` with `common/llm.py` and move `base_url`, `api_key_env`, `model`, `extra_body` into the shared `llm:` section (P4's section already uses the same provider, model and `OPENROUTER_API_KEY`). Keep its cost guard, cache and `--call` safety switch, or confirm `llm.py` provides the same.

---

## 4. `gold` is now published through a gate (affects everyone writing gold tables)

On `main`, P1's tables are built into the `gold_stage` schema, and `gold/publish.py` copies them into `gold` only if every blocking rule in `contracts/c360_customer.yaml` passes.

- [x] Tables owned by others (`gold.text_features`, `gold.nba_decisions`, `gold.nba_overrides`, `gold.audit_log`) are **not** touched by publish: they keep being written straight to `gold`. Check nobody writes P1's tables (`customer_xref`, `c360_*`, `case_customer`, the five views) directly. Checked 4 Oct (after the P2 merge): no code outside `factory/gold/` writes any of them or `semantic_*`.
- [x] Code that reads P1's tables should read `gold.*`, never `gold_stage.*`. Checked 4 Oct: only the builders (`factory/identity`, `factory/gold`, `factory/quality`) touch `gold_stage`.
- [x] After merging, run `python -m trustissues.factory.pipeline --from text_controls` once and check `gold._publish_log` shows `published = true`. Done for the P4 merge; after the P2 merge the semantic build was published the same way (4 Oct, `published = true`, 17 tables, 18 s).

---

## 5. Defaults built on `main` that the team still has to confirm

Full list with reasons: `docs/consumer_views.md` §1. Summary:

- [ ] P3: `label_broken`: `partially_kept` counts as not broken (follows the certified `broken_promise_rate`).
- [ ] P3: `decisioning_customer` is a point-in-time history table; join on `available_from <= ptp_created_ts`.
- [ ] P3: CRM attributes are NULL before the CRM version started (no back-filling); this leaves pre-2022 promises without CRM features.
- [ ] Everyone: a collections case belongs to the holder of the accounts it lists when that disagrees with its CRM ID. Use `gold.case_customer` to join cases to people (1,265 cases disagree; 11,137 cases name a CRM ID that does not exist, and 11,125 of those are tied to a person through their accounts).
- [x] P2: `semantic_*` metric shapes are named and built (P2 merge, 4 Oct). `src/trustissues/ask/data/semantic.py` `SCHEMAS` fixes six views, their grain and every column type (`gold.semantic_cases`, `_promises`, `_contacts`, `_accounts`, `_snapshots`, `_payroll`; grain table in `docs/semantic_views.md`). The 17 metric templates in `ask/data/metric_sql/` read only these. Building them in the warehouse is §6.
- [ ] P2: `bucket_transitions` is quarter to quarter. P2's `roll_rate` does not read it: it pairs two `gold.semantic_snapshots` dates itself. Confirm nobody else needs the quarter grain, or drop it.
- [ ] All: consent column names (`consent_phone` = CRM `consent_call`, `consent_mail` = CRM `consent_letter`).

---

## 6. P2 (`origin/p2-branch-ask-layer`) merged into main (4 Oct, branch `merge/p2-into-main`)

Conflicts, all resolved by keeping both sides:
- [x] `config/settings.yaml`: P2's `duckdb:` and `llm:` sections were already on `main` (from P4); the only difference was spacing in two comments. Main's file kept as is, so it keeps P3's `p3:` section. No diff against `main`.
- [x] `pyproject.toml`: P2's dependency list (adds `sqlglot`, `networkx`, `fastembed==0.8.1`, `pyahocorasick==2.3.1`) plus main's `p3` and `app` extras; P2's `package-data` for `ask/data/metric_sql/*.sql` merged without a conflict.
- [x] `common/interfaces.py`: both sides had added the same field, `RagRequest.case_id`. Kept once; its comment now says what P4's code does (that case's items are read first). `tests/test_interface_stubs.py` already expected `case_id` on both sides, so it did not change.
- [x] P4's files carried by P2 through an older merge (`ask/rag/*`, `common/llm.py`, `common/audit.py`, `common/language.py`, `scripts/ask_policy.py`, `scripts/policy_dashboard.py`): byte-identical to `main` after the merge (`git diff origin/main` empty for all of them).

Overlaps checked:
- [x] Dependencies: `fastembed` (P2's router: local metric matcher, ONNX on CPU) and `sentence-transformers` (P4's optional vector search and reranker in `ask/rag/retrievers.py`, imported only when `policy_rag.mode` is `hybrid*`; the default is `whole_corpus`, and it is not in `requirements.txt`) do different jobs and do not clash. Both kept. Installed into `.venv`: `networkx 3.6.1`, `fastembed 0.8.1`, `pyahocorasick 2.3.1` (`sqlglot` was already there through Splink).
- [x] `governance/masking.py`: column masking by role is new and overlaps nothing. Its free-text part, `mask_text()`, was a second regex scrubber (own patterns, `[SIN]` where `scrub()` writes `[ID]`, no Luhn check, no DOB or long-digit rule). It now calls `text_controls.scrub()`, so there is one scrubber. `tests/test_masking.py` updated (`[ID]`).
- [x] DQ checks: P2 does not keep a second check list; `config/dq_checks.yaml` stays the only one and `dq_report.py` is unchanged.
- [x] `tests/test_p3_sources.py` failed after the merge: it looked for the word `protected_attributes` in every module, and P2's `ask/llm_screen.py` uses that word as a refusal category (it reads nothing). The test now looks for an actual read of the source (`protected_attributes(`, `SOURCES["protected_attributes"]`, `relation(..., "protected_attributes")`).
- [x] 5 P2 router tests need the local metric matcher model, which is not in git. They now skip with "run `python scripts/setup_router.py` first" (the repo's pattern for missing local resources) instead of failing. Setup step added to `requirements.md` §2.
- [x] **Decided 4 Oct: one gate (P1's), P2's additions folded in.** Was: two runners for the c360 contract and two quality reports. P1: `contract_checks.run()` (YAML rules: Q0-Q5 blocking, Q6-Q9 warnings; drives `gold/publish.py`), `quality/anomaly.py` (wired into the pipeline, `config/anomaly.yaml`), `dq_report.py`. P2: `contract_checks.run_checks()` (8 hard-coded rules, 1-5 blocking, `not_checked` when evidence is missing), `quality/ops_anomaly.py` (three-sigma over supplied observations), `quality/check_report.py` (Markdown of both). P2's versions are tested on fixtures but called by nothing; P2's handoff asks for `run_checks` to gate publishing. P2's 8 rules repeat P1's Q1-Q9 one for one (same blocking split); `run_checks` could not gate as written (without a source inventory it returns `not_checked`, and `publish_allowed` then blocks every publish), and P1's `anomaly.py` already covers what `ops_anomaly.py` does and is wired. So: the two things P2 added went into the YAML, Q7 now also reconciles `total_overdue` with the accounts and new warning Q10 flags a build older than 24 hours (batch clock, `refresh_ts`); measured on the published 4 Oct build, Q7 still counts 18 (0 overdue mismatches, 0 negative) and Q10 counts 0, so publishing does not change. `run_checks`, `check_report.py`, `ops_anomaly.py` and `tests/test_quality.py` removed; `load_contract(path)` kept. Test: `tests/test_contract_merge.py`.

Link-ups:
- [x] `ask/rag_handler.py`: `rag_corpus` -> `ask/rag/answer.answer_policy` (as before), `rag_customer` -> `ask/rag/customer_path.answer_customer` through `RagRequest`/`RagResponse` (I6). Needs `make_rag_handler(index, llm, con=...)`; without `con` it says the history is not connected.
- [x] `ask/access.case_customer_resolver(con, as_of)`: resolves a case on the published warehouse from `gold.case_customer` and the agent assigned on the as-of date (P4's `resolve_case`, `case_assignee`). P2's `connection_resolver` reads `gold.semantic_cases`, which exists only in the Ask dev database.
- [x] Refusal wording: P4's re-check refuses with "Customer not found or not visible.", P2's router with "Case not found or not visible." A team lead asking about a case outside their team got P4's wording, a missing case P2's, which told them the case exists. The handler now refuses with the router's wording (`access.NOT_VISIBLE`). Tests: `tests/test_ask_rag_handler.py` (7 new, end to end through `ask()` on P4's customer-path fixture).
- [x] **Decided 4 Oct: P2's spec for `case_id`.** A named case limits the customer path to that case's items (`fetch_history(..., case_id=)`, filtered in SQL); no items on the case is `not_found` with no model call, and the coverage line says when the case has no recorded call. Payments carry no `case_id` in the timeline (1,107,864 rows), so case-scoped answers do not see them. Tests in `tests/test_customer_path.py` and `tests/test_ask_rag_handler.py`. Details: `docs/interfaces.md` "Case to call".
- [x] Built `gold.semantic_*` in the warehouse (4 Oct). `views.semantic_views()` (a builder in the `views` stage) writes the six tables to `gold_stage`, cast to P2's `SCHEMAS`; publish copies them to `gold`. P2's column definitions; the person on each row from P1's identity (`case_customer`, the promise/contact owner rule, `c360_account`) instead of the CRM ID in the xref. New blocking rule Q11 checks the six shapes exactly. Built in 6 s (17,508,567 rows), published in 18 s, all blocking rules pass. Rows without a person: 12 cases, 19 promises, 1,204 contacts. Checked on the published warehouse: P2's benchmark without the LLM gives the same answer as P2's dev run (`eval/repro_runs/run_e.csv`) for every certified metric (BQ-015 adds the small-group note); BQ-017 fails in both with the same numbers. A real case question through `ask()` (BQ-016's case, its assigned agent, a stand-in model) read 17 items, all on that case, including transcript 5010384; another agent was refused. Tests: `tests/test_semantic_views.py`.
- [ ] `scripts/run_benchmark.py` passes no case resolver and no `con`, so benchmark customer questions answer "no case resolver is connected". It now runs on the published warehouse (`--database warehouse/trustissues.duckdb`); wiring the resolver needs a decision first: it asks as `benchmark-lead` (a team lead), whom P4's re-check refuses for a case outside their team. Ask as the case's assigned agent, or give the benchmark user its own rule?
- [x] Masking by role is applied where cards are shown: the app's Ask page calls `mask_answer_card` (4 Oct). Checked against the benchmark answers on the warehouse: no real answer column is masked for any role.
- [x] **Ask page in the app** (`app/pages/1_Ask.py`, 4 Oct; the plan gives Ask to P4, who may restyle it). One question box for every role: `ask()` with the app's warehouse connection, P2's router, P4's RAG (`make_rag_handler(index, llm, con=)`), `case_customer_resolver`, model calls logged to `gold.audit_log`. Staff role -> Ask role: team lead -> `team_lead`, QA analyst -> `analyst`, everyone else -> `agent`. Shows the path (certified / uncertified / policy / customer history / refused), the table, notes, sources and the SQL with its parameters. Without an API key, metric questions and refusals work; model paths say "I could not answer this: language model unavailable ..." (P2's `_rag_card` used to say the sources lacked the answer). Two fixes it needed, both found in the browser: `execute()` now accepts the app's file connection inside a READ ONLY transaction on its own cursor, and P4's policy search reads hardship rows through the caller's connection instead of opening a second one (DuckDB refuses that in a process that holds the file). Policy index built (51 chunks from 19 documents) and added to `make nightly` as stage `policy_index`. Tests: 3 AppTest tests on the real warehouse (`tests/test_app.py`), plus `test_execute`, `test_policy_search`, `test_ask_rag_handler`.
- [x] **Decided 4 Oct: yes, today's queue counts like an assignment.** The question was: may an agent ask about a case the queue allocated to them today? Ask (P2's router and P4's re-check) follows `case_assignment_history` on the as-of date. P3's queue also allocates unassigned or overflow cases: of 46,660 recommendations with an agent on 28 Sep, 28,074 go to the case's assigned agent; for the rest Ask refuses the agent the queue sends to the customer (4,239 of those cases have no assignment at all). The page's example question uses a case the agent holds. Done: P2's `check_case` accepts the case's queue agent (`CaseOwner.queue_agent_id`, filled by `case_customer_resolver` from `gold.nba_decisions` on the as-of date), and P4's `can_view_customer` also allows an agent the queue sends to the customer that day, and that agent's team lead. Same rule as the Decision Card (`agent_view.can_see`). A queue row from another day does not count; without the queue table, assignment alone decides. Tests: `test_customer_path.py`, `test_ask_rag_handler.py`, and `test_app.py` on a real queue case nobody is assigned to.

Tests after the merge: 970 passed, 6 skipped (`main` before: 357 passed, 6 skipped; P2 adds 613 tests). After the two decisions: 967 passed, 6 skipped (P2's 6 `test_quality` tests removed with the code they tested; 3 added). The 6 skips are P4's policy-search tests that need `python -m trustissues.ask.rag.policy_ingest`, as on `main`.
