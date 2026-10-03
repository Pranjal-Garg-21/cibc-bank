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

**On hold (4 Oct, P4 merge):** `common/llm.py` is now on `main`, but its default provider is OpenRouter's free Nemotron and identity pairs carry unmasked PII. Decided to leave `second_opinion.py` unchanged and not run it against any provider (Anthropic included) until the team agrees which provider may receive these pairs. Grey-zone pairs stay with human review (the safe default).

`src/trustissues/factory/identity/second_opinion.py` (on `main`) calls the Anthropic SDK directly, because `common/llm.py` did not exist when it was written. The plan says every LLM call goes through `common/llm.py` (caching, prompt version, audit).

- [ ] Once `common/llm.py` is on `main`, change `second_opinion._ask()` to call it, and drop the `identity.llm_model` / `llm_effort` settings in favour of the shared `llm:` section.
- [ ] **Team decision:** identity pairs contain unmasked names, dates of birth, phone numbers and postal codes, because those are what the matcher compares. Agree which LLM provider may receive them before running it (3,343 pairs waiting).
- [ ] Keep the safe default: if the call fails or no key is configured, pairs go to the human review queue and records stay separate.
- [ ] Same for `src/trustissues/factory/curate/mapping_assistant.py`: it calls OpenRouter (Nemotron 3 Super) itself through `_post()` with its own `mapping_assistant:` section in `config/settings.yaml`. Replace `_post()` with `common/llm.py` and move `base_url`, `api_key_env`, `model`, `extra_body` into the shared `llm:` section (P4's section already uses the same provider, model and `OPENROUTER_API_KEY`). Keep its cost guard, cache and `--call` safety switch, or confirm `llm.py` provides the same.

---

## 4. `gold` is now published through a gate (affects everyone writing gold tables)

On `main`, P1's tables are built into the `gold_stage` schema, and `gold/publish.py` copies them into `gold` only if every blocking rule in `contracts/c360_customer.yaml` passes.

- [ ] Tables owned by others (`gold.text_features`, `gold.nba_decisions`, `gold.nba_overrides`, `gold.audit_log`) are **not** touched by publish: they keep being written straight to `gold`. Check nobody writes P1's tables (`customer_xref`, `c360_*`, `case_customer`, the five views) directly.
- [ ] Code that reads P1's tables should read `gold.*`, never `gold_stage.*`.
- [ ] After merging, run `python -m trustissues.factory.pipeline --from text_controls` once and check `gold._publish_log` shows `published = true`.

---

## 5. Defaults built on `main` that the team still has to confirm

Full list with reasons: `docs/consumer_views.md` §1. Summary:

- [ ] P3: `label_broken`: `partially_kept` counts as not broken (follows the certified `broken_promise_rate`).
- [ ] P3: `decisioning_customer` is a point-in-time history table; join on `available_from <= ptp_created_ts`.
- [ ] P3: CRM attributes are NULL before the CRM version started (no back-filling); this leaves pre-2022 promises without CRM features.
- [ ] Everyone: a collections case belongs to the holder of the accounts it lists when that disagrees with its CRM ID. Use `gold.case_customer` to join cases to people (1,265 cases disagree; 11,137 cases name a CRM ID that does not exist, and 11,125 of those are tied to a person through their accounts).
- [ ] P2: `bucket_transitions` is quarter to quarter; `semantic_*` views wait for P2 to name the metric shapes.
- [ ] All: consent column names (`consent_phone` = CRM `consent_call`, `consent_mail` = CRM `consent_letter`).
