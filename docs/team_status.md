# Team status: what is running on Anushka's machine (P1)

**Snapshot taken: 2026-10-03 13:12 IST.** This is a point-in-time note so nobody repeats work. It goes stale. Check the time above, and ask Anushka (or look at `warehouse/curate.log` on that machine) before relying on it. Anushka owns this file and updates it; delete it when the curated build is finished and pushed.

Plan of record: [`implementation_plan.md`](implementation_plan.md). What the code does: `docs/documentation.md` (**not on `main` yet**, it comes with the next push).

---

## 1. What is running right now

**One thing only: the curated build.**

| | |
|---|---|
| Command | `python -u -m trustissues.factory.curate.build_curated` |
| Process | PID 37272, started 11:38, about 112 CPU-minutes used, 2.7 GB RAM |
| Doing | building `curated.deposit_accounts` (started 12:48); tables are built alphabetically |
| Output | `warehouse/curate.log` (one line per finished table) |
| Lock | The process holds the **only write lock** on `warehouse/trustissues.duckdb` (6.4 GB). Nothing else on that machine can open the file. |

Nothing else is running: no raw load, no dictionary step, no tests, no servers, no second DuckDB session.

**The warehouse file is not shared.** It lives only on Anushka's machine, is gitignored, and is not available to anyone else. Each teammate builds their own (the setup guide `requirements.md` is **not on `main` yet**; until it is, the steps are: `pip install -e ".[dev]"`, download and unzip the dataset into `maple_data/`, run `python -m trustissues.factory.ingest.load_raw`, then `python scripts/verify_warehouse.py`).

### Curated build progress

| State | Tables |
|---|---|
| **Done (15)** | `account_monthly_snapshot`, `agent_notes`, `agent_shifts`, `agents`, `benchmark_questions`, `bureau_history`, `call_transcripts`, `card_accounts`, `card_statements`, `case_assignment_history`, `channel_capacity`, `code_lookups`, `collections_cases`, `contact_history`, `customers` |
| **In progress** | `deposit_accounts` |
| **Waiting (15)** | `external`, `geo_reference`, `hardship_programs`, `loan_accounts`, `loan_instalments`, `metric_definitions`, `model_scores`, `offers`, `ops_events`, `promises_to_pay`, `qa_checklist`, `reference_documents`, `salary_credit_history`, `transactions`, `voice_samples` |

Heads-up for P3: **`curated.promises_to_pay` does not exist yet.** It is in the waiting group and is small once reached, but the build reaches it only after `loan_accounts`, `loan_instalments` and `model_scores`.

**Estimated finish: roughly 14:15 to 14:45 IST. This is a rough estimate** (the build is much slower than the size of the tables predicts, so earlier estimates were too optimistic). Treat it as "about an hour and a half from now", not a promise.

### Results so far (real data)

- Raw load: all 31 tables match the row counts the release publishes. 25,000 transcript JSON files loaded. No blocking schema drift.
- Curated: **0 exact duplicate rows in every table so far, and 0 failed type casts** except 1 column in `account_monthly_snapshot`. So the data's duplicates are **business-level** (for example the same person with two CRM IDs), not copied rows. Identity matching has to find them.
- Build times: `customers` 41 min, `contact_history` 14 min, `card_accounts` 9 min, `card_statements` 2 min, `account_monthly_snapshot` 1.6 min.

## 2. Already done: please do not repeat

| Done | Where | Note |
|---|---|---|
| Raw load of all 31 tables | `raw.*` in Anushka's warehouse | Code is on `main`. **You do need to run it once on your own machine**, that is expected, not duplication. About 35 min |
| Raw ingest code, schema-drift check, manifest | on `main` (commit `e4d516c`) | Do not rewrite |
| Data dictionary parsing and sensitivity labels | on `main` | Do not rewrite. Generated `config/sensitivity.yaml` is **not** pushed yet; generate your own with the dictionary step |
| Overdraft columns classified | `config/sensitivity_overrides.yaml` | Decided: `deposit_accounts.overdraft_dpd` and `overdraft_dpd_bucket` are usable (`none`) |
| Standardisation functions (DOB, phone, name, postal, employer) | local commit `cdd2c81`, **not pushed** | 18 tests. Do not rewrite |
| Curated builder | local commit `cdd2c81`, **not pushed** | Works on 15 real tables so far |
| Findings about source formats, dictionary vs data, classification gaps | `docs/documentation.md` section 6 (in local commit `23fc7b1`, not pushed) | Ask rather than re-investigate |
| Downloading the dataset, the `hf` PATH problem, `tar` instead of `unzip` on Windows | `requirements.md` (local commit `23fc7b1`, not pushed) | Solved. In short: the command must be on one line with the folder right after `--local-dir`; on Windows `hf.exe` is not on PATH (call it by full path or add the Scripts folder); use `tar -xf` instead of `unzip` |

## 3. What is on GitHub and what is not

| | State |
|---|---|
| On `origin/main` | Raw ingest commit `e4d516c` (code, tests, `requirements.txt`, `pyproject.toml`, `scripts/verify_warehouse.py`) |
| **Local only, not pushed** | `cdd2c81` curated layer and `pipeline.py`; `23fc7b1` `requirements.md` and `documentation.md`; `config/sensitivity.yaml` |
| Why held back | The curated code has not finished a full run yet. It will be pushed after it does and `verify_warehouse.py` passes |

## 4. Not started (Layer 1, owned by P1)

Identity matching (nodes, exact rules, Splink, LLM second opinion, review queue, clusters), gold layer (`c360_customer`, survivorship, views, publish), data-quality checks, `contracts/c360_customer.yaml`, `text_controls.py`, `mapping_assistant.py`. **P1 owns these. Ask Anushka before starting any of them**, so two people do not build the same thing. Splink is not installed anywhere yet.

## 5. Things to know before you run anything yourself

- **Do not run `build_curated` (or `pipeline`) on your machine yet unless you need curated tables.** On the reference machine it takes well over an hour, because wide tables are slow. A speed-up is planned but **not started**. Until it lands, prefer working on `raw.*` or waiting for the push.
- **DuckDB allows one writer at a time.** Do not share one `.duckdb` file between people or processes. Copy it if you want someone else's build, then verify it.
- **Do not put the repo in a OneDrive-synced folder** if you can avoid it (Anushka's is, and the 6 GB database syncs).
- The dataset is **synthetic**. All names, IDs and phones are fake.

## 6. Open items on Anushka's side

1. Speed up the curated build's typing and failure-scan step (about 63% of the time on a sample), then use it for the remaining wide tables. Not started.
2. When the build finishes: re-run the dictionary step (applies the overdraft decision and labels the derived columns), run `scripts/verify_warehouse.py`, write `docs/curation.md` with the real results.
3. Push the curated and docs commits.
4. Plan section 11 "verify once the data is loaded" questions (bureau key vs deposit key, `redaction_status = none`, transcript retention source, hardship policy join, test-cell results). Not started.

## 7. Machine facts (for comparing timings)

Windows 11 Home, 15.7 GB RAM, Python 3.14.6, DuckDB 1.5.6, repo inside a OneDrive folder, about 190 GB free disk.
