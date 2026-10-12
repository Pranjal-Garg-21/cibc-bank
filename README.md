#CIBC: Collections 360 and Next Best Action

This project is a local data and decisioning platform for collections operations. It turns 31 fragmented source tables
into one governed customer 360 record, lets staff ask controlled business questions in plain English, and recommends
the next best treatment, channel and timing for every open collections case.

The system combines a reproducible data pipeline, identity resolution, data-quality gates, point-in-time-safe features,
machine-learning risk models and a role-aware web console. Answers expose their SQL or policy sources, customer-facing
actions show their reasons and safeguards, and decisions that need judgement remain with a person. As of 28 September
2026, the built warehouse contains 1,011,960 people from 3,487,885 source customer records.

## What we built

### Governed customer 360

The Layer 1 factory ingests CSV, Parquet and JSON transcript data into DuckDB, creates typed and deduplicated
`curated.*` tables, runs YAML-driven quality and anomaly checks, applies text controls, resolves identities with
deterministic rules and Splink, and publishes contract-checked `gold.*` customer and case views. It produces data
quality, anomaly, identity and publish reports under `reports/`.

### Controlled insight service

The Ask service supports certified portfolio metrics, metric drivers, fixed single-case facts, policy questions,
customer-history questions and validated read-only SQL for questions without a certified path. Certified answers expose
their definitions and SQL. Policy answers cite their sources. Generated SQL is labelled uncertified, restricted to
approved read-only queries and subject to row and time limits. Refusals, disclosure controls and access checks run
before an answer is returned.

### Next-best-action decisioning

Layer 3 creates point-in-time-safe structured and text features, with the same SQL definitions used for training and
scoring. Layer 4 uses LightGBM case-cure and promise-break models, SHAP explanations, action values, capacity limits
and channel constraints to create a queue. Hard stops, hardship and vulnerability rules are applied before an action
is recommended. Decision cards show evidence, blocked actions and required approvals.

### Role-aware web console

The FastAPI server serves a React and TypeScript console with Ask, work queue, decision card, team and approvals,
hardship, identity review, data status, audit and authorised table-browsing workflows. Server-side role checks are
deny-by-default; the browser does not choose its own role. Passwords, MFA, session controls, PII masking, rate limits
and audit logging are included.

## Technology stack

| Area | Technology | Use in the system |
|---|---|---|
| Language | Python 3.10+ | Data processing, services, modelling and automation |
| Analytical database | DuckDB | Local warehouse across `raw`, `curated`, `gold` and feature layers |
| Data formats | CSV, Parquet, JSON and YAML | Source data, intermediate data, configuration and contracts |
| Data processing | Python, SQL, OpenPyXL, PyArrow, NetworkX | Ingestion, standardisation, relationships and file interchange |
| Data quality | Custom YAML-driven checks | Schema validation, anomaly detection and publish gates |
| Identity resolution | Splink, deterministic rules and SciPy | Customer linkage and golden-record construction |
| Feature store | Feast offline store | Point-in-time-safe feature definitions and registry |
| Machine learning | LightGBM, scikit-learn, SHAP and pandas | Risk models, calibration, evaluation and explanations |
| Question answering | SQL templates, sqlglot, BM25 and FastEmbed | Certified metrics, safe SQL validation, routing and policy retrieval |
| Language models | OpenAI-compatible API through OpenRouter; optional Anthropic support | Optional model fallback, policy answers, summaries and generated SQL |
| Backend | FastAPI and Uvicorn | API, authentication, sessions, RBAC and serving |
| Frontend | React 19, TypeScript 5.9, Vite 8 and Tailwind CSS 4 | Web console |
| UI and data access | Radix UI, Lucide React, TanStack Query and React Router | Accessible controls, icons, API state and navigation |
| Testing | Pytest, Vitest and TypeScript compiler | Unit, integration, security, API and frontend checks |
| Operations | Make, `scripts/nightly.py` and local filesystem | Repeatable builds, training, queue generation and monitoring |

This implementation intentionally uses a local DuckDB and file-based workflow. Redis, Spark, Chroma, Great
Expectations and an orchestration service are not required to run it.

This README covers **how to run the system** (section 1), **what is implemented** (section 2), **which AI tools are
used and where** (section 3), and **where the supporting deliverables live** (section 4).

---

## 1. How to run it

Everything runs on one machine. Allow about 30 GB of disk and 16 GB of RAM. Linux, macOS and Windows work; heavy steps
run under a memory cap on Linux (the `Makefile` does this for you). Do not keep the repo in a OneDrive, Dropbox or
iCloud folder. The detailed version, with troubleshooting, is `docs/teammate_setup.md` (every command from `git pull`
to a working console) and `requirements.md` (install, data and build in depth).

### 1.1 Install (once)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,p3,app]"                          # backend, models (LightGBM, Feast) and the console's server
python scripts/setup_router.py                          # Ask's local metric matcher (65 MB, downloaded once)
```

Node.js 20 or newer is needed once, to build the web console.

### 1.2 The `.env` file (only needed for the language model)

Create a file named `.env` in the repository root, copied from the template:

```bash
cp .env.example .env        # then put your key in .env
```

It looks like this (one line, no quotes needed):

```
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

- This is the only value the code reads from `.env`. `common/llm.py` loads it when the first model call is made and
  never overrides a variable already set in your shell. The variable name comes from `llm.api_key_env` in
  `config/settings.yaml`.
- `.env` is gitignored. Never commit it, paste it into a chat or print it.
- The model is NVIDIA Nemotron 3 Super on OpenRouter (`nvidia/nemotron-3-super-120b-a12b`, set in
  `config/settings.yaml`). To use Anthropic's API instead, set `llm.provider: anthropic` there and put
  `ANTHROPIC_API_KEY=...` in `.env`.
- Without a key nothing breaks: the build, the queue, certified metrics, single-case facts and refusals all work.
- Two optional paths are ordinary shell environment variables, not `.env` entries:
  `MAPLE_DATA_DIR` (where the release data is) and `WAREHOUSE_DIR` (where the database goes, for example outside a
  synced folder). The defaults are in `config/settings.yaml`.

### 1.3 Data

Download the release (Hugging Face dataset `nuxsh/maple-collections-hackathon`) and unzip it into `maple_data/`
exactly as `requirements.md` section 3 says. It lands at `maple_data/maple_collections_release/`.

### 1.4 Build the warehouse

```bash
make pipeline                                                     # raw -> curated -> quality -> identity -> gold (about 10 minutes after the first load; the first load takes about 35 minutes)
python scripts/second_opinion_manual.py import --labeller luna    # the committed identity verdicts (no model call)
python -m trustissues.factory.identity.build --from relationships # rebuild people with those verdicts
python -m trustissues.factory.pipeline --from gold                # gold, views, publish gate, identity report
```

The last command prints the publish result. Every blocking rule of the data contract must pass, and the identity
report should say about **1,011,960 people** (the matcher retrains on each machine, so a handful either way is
normal).

### 1.5 Models and the nightly queue

```bash
make train          # training sets, point-in-time features, promise and case models, action values, Feast registry
make nightly        # pipeline, policy index, shared text features, today's queue, fairness, monitoring
```

On the 4 October build this produces 86,059 recommendations for 28 September, 13,522 of them support calls. Neither
command calls a language model (see the notes in 1.8).

### 1.6 The web console

```bash
make ui-build                                      # once: builds ui/web/dist
python ui/serve.py --demo --shared-access          # asks for a demo password at start-up; http://127.0.0.1:8000
```

Sign in with any active staff ID from `curated.agents`. The server reads the person's role from the data, and the
console shows that role's screens: agents see their own cases, team leads see their team and approve, the hardship
desk sees hardship and vulnerability work, and QA sees review and audit screens. Individual accounts with
two-factor sign-in are described in `ui/README.md`.

**Add `--ask-llm`** to let the Ask page use the language model:

```bash
python ui/serve.py --demo --shared-access --ask-llm
```

Without that flag no model is called. Certified metrics, single-case facts and refusals still answer; policy
answers, call summaries and generated queries say they need the model. The flag needs `OPENROUTER_API_KEY` in `.env`
(never committed).

### 1.7 Check it and reproduce the benchmark answers

```bash
python -m pytest -q                       # about 1,300 tests; some skip when a model or warehouse is needed
python -m pytest -q tests_integration     # 95 checks written independently of the application code, on your warehouse
RUN_LLM_TESTS=1 python -m pytest -q tests_integration   # also the model-backed checks (calls the model)

python scripts/run_benchmark.py --database warehouse/trustissues.duckdb --llm \
    --extra eval/benchmark_questions_extra.csv --out submission/benchmark_answers.csv
python scripts/score_dev.py submission/benchmark_answers.csv      # development questions scored against the answer key
```

`submission/benchmark_answers.csv` holds the answers to all 50 released questions (the 35 released at the start and the
15 released at 19:00 on 4 October), written by the same `ask()` function the console calls, in one run and never edited
by hand. Expected on the development questions: 14 pass, 6 text answers marked for a person to confirm, 1 fail. The
failure is BQ-017, where the reference answer's date parser drops 418,319 month-first dates; with them parsed, our
numbers match exactly.

### 1.8 What not to run

- `make nightly NIGHTLY_FLAGS=--with-text-features` or `python -m trustissues.features.text_extract` send customer
  notes to the configured model provider. The team has not agreed a provider for note text, so the shared text
  features come from committed labels instead (section 3).
- Do not switch on `identity.llm_enabled` in `config/settings.yaml`. It would send unmasked identity pairs to a provider.
- Never commit `warehouse/`, `maple_data/` or `.env`.

### 1.9 Where things are

| Path | What |
|---|---|
| `src/trustissues/factory/` | Layer 1: load, curate, quality, identity matching, gold tables, publish gate |
| `src/trustissues/ask/` | Layer 2: question routing, refusals, certified metrics, generated SQL, policy and case answers |
| `src/trustissues/features/`, `nba/` | Layers 3 and 4: features, models, next-best-action queue, review |
| `ui/` | Web console (FastAPI server in `ui/tiui/`, React app in `ui/web/`) |
| `contracts/c360_customer.yaml` | The data contract for the golden customer record; it also drives the publish gate |
| `config/` | Settings, sensitivity classes, refusal lexicon, data-quality checks |
| `reports/` | Data quality, anomaly, identity, fairness, model and monitoring reports (regenerated by the pipeline) |
| `eval/` | Evaluation sets and results (router, SQL stress, end-to-end unseen questions, reproducibility runs) |
| `docs/` | Design sections per layer, build notes and decisions |

---

## 2. Implementation details and design decisions

The sections below record important implementation choices, differences from the original design and current limits.
The opening sections describe the product at a high level; this section preserves the detail needed to understand the
trade-offs behind the build.

### Layer 1: data product factory

| Design said | Built | Why |
|---|---|---|
| Great Expectations for data quality | Our own checks driven by YAML (`config/dq_checks.yaml`) and a data contract that gates publishing: seven blocking rules (identity, safety flags, interface shape and more) and five warnings | We never used Great Expectations, and the contract gives one file that documents the product and runs as the gate |
| LLM second opinion on uncertain identity pairs (Splink score 0.80 to 0.95) | The model call is switched off (`identity.llm_enabled: false`). The 3,342 uncertain pairs were judged offline by GPT Luna-6.1 and its verdicts applied through the same path; pairs still undecided go to the review queue | No provider was approved for unmasked identity data. The result is 1,011,960 people instead of 1,013,152 |
| AI drafts column mappings and SQL | A mapping assistant exists and is tested offline but was never run against a model | Time; it was not run against a model API |
| AI flags unusual row counts and null rates | Statistical anomaly detection: row counts and null rates per slice against the same weekday, explained by the operations log and holidays. No model | Rules explain a day better than a model does, and they are reproducible |
| Data quality report | A generated report (73 issues, each with the records affected and how it was handled) plus a pass/fail summary of the contract after every build | Added: it comes straight from the pipeline, so it cannot drift from the data |

### Layer 2: insight and NLP

| Design said | Built | Why |
|---|---|---|
| A trained prompt-injection classifier | Rule patterns, plus a model safety screen that **can only add a refusal** | No classifier was trained. A rule refusal is final, and a model failure leaves the rules' decision |
| Three paths: data, customer, policy | Eight outcomes: certified metric, drivers ("why did it change"), policy, customer call, **single-case facts**, generated SQL, incident and cannot-answer | Real questions needed more: a portfolio metric cannot be filtered to one case, so a case's status, balance, days past due, assigned agent and next best action get their own fixed lookup |
| The model fills filters and dates for the certified metric | Periods and breakdowns are parsed in code; the model may only choose which metric | A model never supplies a number or a date, so a certified answer is reproducible |
| Metric chosen by a model | A local embedding matcher (`bge-small-en-v1.5`, no question leaves the machine) behind trigger rules, with a model fallback for what remains | Rules alone scored 47% on wording we had not seen; with the matcher and fallback, 90 to 97% on unseen sets |
| SQL fallback sees about 15 columns out of 1,422, chosen by schema matching and a shortest join path | The prompt lists all 73 approved columns with notes, plus join hints as text. The validator enforces approved join keys | The approved set was small enough to show whole, and the validator, not the prompt, is the control |
| Validator row limit of 100 | 500 rows returned and a 5-second limit per statement | Larger breakdowns fit, and the limit stops a runaway scan |
| Policy search with Chroma and embeddings | No vector store. Every policy section in force on the as-of date (51 chunks, about 4K tokens) goes to the model, filtered by version and role; every answer must quote and cite a source or say "not found". BM25, hybrid and hybrid-plus-rerank search are built and selectable (`policy_rag.mode`) | We compared them on 16 questions with a known correct section: whole-corpus 16/16, hybrid with rerank 15/16, BM25 13/16. The corpus is small enough to read whole, so nothing can be missed by retrieval |
| Editable filter chips and suggested follow-ups on each answer | Each answer carries its SQL or sources, a certified flag and notes ("uncertified", "one repair", "period after the as-of date"). Chips and follow-ups are not built | Time |
| Answers in 1 to 2 seconds | Measured: 0.2 s for a cached certified metric, 1.5 to 2.5 s uncached, 3 to 8 s for generated SQL | The safety screen adds one model call per question |
| (not in the design) | Small-group disclosure notes for tiny breakdowns, recorded incident reports cited in "why" answers (for example the 23 to 24 September dialer outage), a randomised-pilot variant of the cure rate | Found while answering the released and unseen questions |

### Layer 3: feature store

| Design said | Built | Why |
|---|---|---|
| Feast with Redis as the live store | Feast registry with the offline store only; the nightly batch computes features from the same SQL files | Nothing reads a live store, so Redis would only have been another service to run |
| Text features (hardship, reason for delay, promise strength, dispute) extracted by a model and checked against about 200 hand-labelled notes | Extraction is built and was scored on the release's public labels (921 texts: hardship F1 0.92, stated-reason accuracy 0.84). The 20,000 text features in the warehouse are Luna's, committed as labels and evidence positions without any text | No provider was agreed for note text. Every evidence sentence is checked to appear word for word in the note |

### Layer 4: models and decisioning

| Design said | Built | Why |
|---|---|---|
| One model: the chance a promise breaks | Two: case risk for every open case, and promise break | Only a third of open cases have an open promise |
| Action value from past results of each treatment | Effect measured from the bank's randomised test | Past results are biased: the bank contacts the cases that are not curing |
| Hardship: gentler actions | No reminders; a certified agent's support call offering the programmes | `POL-COLL-004` sections 4.2 and 4.5: offer before asking for payment |
| National Do Not Call List blocks calls | Only the internal list | `POL-COLL-001` section 5.3: the national list covers telemarketing only |
| A model rewords the card, with a fact check | Template text | Time; the template is already plain language |
| Promise coach in real time, re-scoring after each payment, language rule | Not built | Time; next steps |

### Application and governance

| Design said | Built | Why |
|---|---|---|
| Agent screen (the build plan named Streamlit pages) | A FastAPI server with a React and TypeScript console: Ask, work queue, decision card, team and approvals, identity review, data status. The server decides each person's role from the staff data | The console handles approving or overriding single rows, and role views, far better than reruns of a script |
| Writes by people stored with the warehouse | Decisions go to `gold.nba_overrides`, and the console's own access log and model audit go to a separate DuckDB file. The warehouse is opened per request, never held open | DuckDB allows one writer per file; the nightly run must be able to go while the console is up |
| "No AI is used for refusals" | Refusals are decided by rules. The model screen can add a refusal but never remove one | A rule list cannot catch every wording; the screen only adds a safeguard |
| Role-based access, masking, audit | Built as designed: agents see only their assigned cases, a missing case and a hidden case give the same answer, names are masked, and every model call is logged | |

Known limits are listed in `docs/p2_design_sections.md` (section 4 of the Ask design): the safety layers are
heuristics, a certified answer does not prove the question was understood as asked, generated SQL is always labelled
uncertified, and concurrency has not been load-tested.

---

## 3. AI tools we used, and for what

| Tool | What it did | Where it runs | How its output is checked |
|---|---|---|---|
| **NVIDIA Nemotron 3 Super** (through OpenRouter; the free tier at first, the paid tier from 4 October) | API calls from the running system: the router fallback, the safety screen, generated SQL for questions no certified metric covers, policy answers and customer-call summaries | At run time, only when the console starts with `--ask-llm` or a benchmark run uses `--llm`. One wrapper, `common/llm.py`, makes every call and logs it | Certified templates need no model. Generated SQL passes the validator and is labelled uncertified. A policy or customer answer must quote its source word for word and cite it, or it says "not found". The safety screen can only add a refusal |
| **GPT Luna-6.1** (data processing judge) | Judged the 3,342 uncertain identity pairs (match or no match, with a generic reason) and produced the 20,000 text features (hardship, reason for delay, promise strength, dispute, with the evidence sentence) | Offline, outside the running system. The system never calls it | Only verdicts, hashes and evidence positions are committed, never record or note text. Verdicts are applied only if the pair or note still matches by hash. Overlapping pairs were judged blind more than once, and a disagreement became "no match" |
| **Claude and Codex** | Wrote and reviewed code, tests and documentation | During development, under the team's direction | Everything landed through git and the test suites; the integration checks were written independently of the code they test, with their own reference SQL |

What a model does not decide: access checks, SQL validation, refusals by rule, hard stops (cease contact, deceased,
bankruptcy, open dispute), hardship eligibility, or any customer-affecting action, which always needs a person.

**What leaves the machine.** Calls to Nemotron carry the question text, metric definitions and policy text, and for a
customer-call question that case's redacted notes and transcripts, scrubbed again before sending. The pair export that
Luna judged held identity fields; it stays on the local machine and is gitignored, and the running system never sends
unmasked identity data to any provider.

---

## 4. Where the other deliverables are

Copies of everything the hackathon asks for in the repo are in [`deliverables/`](deliverables/):

| Deliverable | File in `deliverables/` | Source of truth |
|---|---|---|
| Architecture diagram | `architecture_diagram.png` | `docs/architecture/full.png` |
| Data contract | `data_contract.yaml` | `contracts/c360_customer.yaml` |
| Data quality report | `data_quality_report.md` | `reports/data_quality_report.md` (regenerate with `python -m trustissues.factory.quality.dq_report`) |
| Benchmark answers | `benchmark_answers.csv` | `submission/benchmark_answers.csv` |

Design sections per layer: `docs/p1_design_sections.md`, `docs/p2_design_sections.md`, `docs/p3_submission_text.md`,
`docs/p4_design_sections.md`.
