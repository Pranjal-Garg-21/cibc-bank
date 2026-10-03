# Team status: what is running on Anushka's machine (P1)

**Snapshot: 2026-10-03 about 13:30 IST.** A point-in-time note so nobody repeats work. It goes stale. Ask Anushka before relying on it. Delete this file once the curated build is finished and pushed.

What the code does: [`documentation.md`](documentation.md). How to build on your own machine: [`../requirements.md`](../requirements.md).

---

## 1. What is running right now

**One thing: the curated build, resumed after a crash.**

| | |
|---|---|
| What happened | The first run built 16 of 31 tables (1.7 hours), then **crashed on `external`**: a bug in my code (a DATE-typed column was passed to a text-only function). Fixed, regression-tested, and the build was restarted for the 15 remaining tables |
| Now running | `sh warehouse/resume_curate.sh`: first the small and narrow tables (including `promises_to_pay`), then `external` and `loan_accounts` last |
| Output | `warehouse/curate2.log` (the first run's output is in `warehouse/curate.log`) |
| Lock | The running process holds the **only write lock** on `warehouse/trustissues.duckdb`. Nothing else on that machine can open it |

Nothing else is running. The warehouse file is **not shared**: it lives only on Anushka's machine and is gitignored. Each teammate builds their own.

**Built so far in the resumed run (all with 0 duplicates, 0 failed casts):** `geo_reference`, `hardship_programs`, `loan_instalments`, `metric_definitions`, `model_scores`, `offers`, `ops_events`, `promises_to_pay`, `qa_checklist`, `reference_documents`. In progress or waiting: `salary_credit_history`, `transactions`, `voice_samples`, then `external` and `loan_accounts` (the wide identity tables, 30+ minutes each).

**P3: `curated.promises_to_pay` now exists in Anushka's warehouse** (238,815 rows, built in 2 seconds). On your own machine it takes seconds too: `python -m trustissues.factory.curate.build_curated --only promises_to_pay`.

**Remaining estimate: roughly 1 more hour**, almost all of it `external` and `loan_accounts`. Check the log, not this file, for the true state.

---

## 2. Already done: please do not repeat

| Done | Where | Note |
|---|---|---|
| Raw load, all 31 tables, verified against the release row counts | Anushka's warehouse | Code is on `main`. **You still run it once on your own machine**, that is expected. About 35 min |
| Raw ingest, schema-drift check, manifest, dictionary and sensitivity labels | `main` | Do not rewrite |
| Standardisation functions (DOB, phone, name, postal, employer), 18 tests | `main` | Do not rewrite |
| Curated builder | `main` | Real-data result so far: 16 tables, **0 exact duplicate rows**, 0 failed casts except 1 column. Duplicate customers are business-level (identity step) |
| Findings on source formats, dictionary vs data, classification gaps | `documentation.md` section 6 | Ask rather than re-investigate |
| Dataset download problems (`hf` PATH, one-line command, `tar` instead of `unzip`) | `requirements.md` | Solved |
| Overdraft columns classified as usable | `config/sensitivity_overrides.yaml` | Decided 2026-10-03 |

## 3. What is on GitHub

Everything except two things: `config/sensitivity.yaml` (generated; run the dictionary step to make yours) and the `warehouse/` data (gitignored).

## 4. Not started (Layer 1, owned by P1)

Identity matching (nodes, exact rules, Splink, LLM second opinion, review queue, clusters), gold layer, data-quality checks, `contracts/c360_customer.yaml`, `text_controls.py`, `mapping_assistant.py`. **Ask Anushka before starting any of them.** Splink is not installed anywhere yet.

## 5. Before you run anything

- **The curated build is slow and uses about 1 core of 12**, so extra cores do not help. Build only the tables you need with `--only` (see `requirements.md` section 4a). Do not run the full build casually.
- **One writer per DuckDB file.** Do not share one `.duckdb` file between people; copy it and verify the copy with `python scripts/verify_warehouse.py`.
- Avoid putting the repo in a OneDrive-synced folder.
- The dataset is synthetic.

## 6. Open items on Anushka's side

1. Finish the resumed curated build, then re-run the dictionary step, run `scripts/verify_warehouse.py`, write `docs/curation.md` with the final results.
2. Find out why the curated build uses only one core, and speed it up. Not started beyond ruling out memory, disk and the cast-failure scan.
3. Plan section 11 data questions (bureau key vs deposit key, `redaction_status = none`, transcript retention source, hardship policy join, test-cell results). Not started.
