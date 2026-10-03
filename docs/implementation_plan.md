# TrustIssues: Implementation Plan

Build plan for the 36-hour build phase (3 Oct 09:00 → 4 Oct 21:00 IST). Source of truth is the final design doc (`docs/document(22)-1.pdf`). The person docs (`p1_`–`p4_*.md`) give finer detail. Where they conflict, the design doc wins; see [§9 Conflicts resolved](#9-conflicts-resolved).

This is a skeleton, not an exhaustive list. Add files when a new purpose comes up, but keep them inside the layer packages below so ownership stays clear.

**Owners (balanced):**
- **P1**: Layer 1 core. Ingest, curate, identity, gold build, decisioning views, publish, pipeline, contract YAML.
- **P2**: Layer 2 structured path (Ask, NL-to-SQL), plus data quality (`factory/quality/`), `semantic_*` views, benchmark runner, Team Lead, Identity Review and Data Quality pages, `masking.py`.
- **P3**: Layers 3–4 (features, model, NBA), plus the Agent Queue and Decision Card pages, `monitoring.py`.
- **P4**: RAG paths, LLM text features, `llm.py` / `audit.py`, Home and Ask pages, `roles.py`, architecture, README, demo and submission.

**Why this split:** P1 had identity resolution (the riskiest piece) plus all of quality and views; P4 had RAG plus every screen. P2's later hours were light, so P2 absorbs P1's quality work and the less NBA-coupled screens. P3 takes the screens that show its own output. Rewording and allocation moved to stretch to protect P3.

---

## 1. Stack and conventions

| Concern | Choice | From |
|---|---|---|
| Engine | DuckDB over Parquet, single machine | Design §3 |
| Identity | Rules in SQL, Splink, LLM second opinion, human review | Design §3 |
| Data quality | Great Expectations, driven by the contract YAML | Design §3, P1 §9 |
| Ask | sqlglot, networkx (join graph), pyahocorasick (refusal gate), Chroma (policy only), LLM | Design §4, P2, P4 |
| Feature store | Feast registry; offline store on Parquet/DuckDB. No online store: Redis was built and removed (nothing real-time reads it; see `p3_plan.md` step 5) | Design §5 |
| Model | LightGBM + SHAP | Design §6 |
| UI | Streamlit multipage app, with a role picker for the demo | Not in docs. Fastest route to a working screen in 36 h |
| LLM | One wrapper (`common/llm.py`). Model IDs live in config. Use a small model for bulk jobs (text features, identity pairs) and a larger one for answers | Not specified in docs |

**Conventions**
- `AS_OF_DATE = 2026-09-28` lives in config. Code never calls `today()` (Design §4).
- Schemas: `raw` → `curated` → `gold`. Consumers read only `gold.*` views.
- One package per layer under `src/trustissues/`. SQL lives in `.sql` files next to the Python that runs it.
- Every LLM call goes through `common/llm.py`, which caches by input hash, records the prompt version, and writes to the audit log.
- `maple_data/`, `warehouse/` and any `*.duckdb` files are gitignored.

---

## 2. Repository layout

```
cibc-trustissues/
├── README.md                     # SUBMISSION: how to run, what changed from design, AI tools used
├── pyproject.toml                # deps + `trustissues` console entry points
├── Makefile                      # make data | pipeline | features | train | queue | app | benchmark | test
├── .env.example                  # LLM API key, MAPLE_DATA_DIR, WAREHOUSE_DIR
├── config/
│   ├── settings.yaml             # AS_OF_DATE, paths, match thresholds (0.95 / 0.80), model IDs, token budgets
│   ├── roles.yaml                # role → row scope, PII masking, policy doc types/confidentiality
│   ├── sensitivity.yaml          # GENERATED from data dictionary: per-column Direct PII / Quasi / Protected + proxy list
│   ├── refusal_lexicon.yaml      # synonyms for protected attrs, PII terms, out-of-scope topics, safe alternatives
│   └── prompts/                  # versioned prompt templates (file name carries version)
├── contracts/
│   └── c360_customer.yaml        # SUBMISSION: data contract (start from release DC-COLL-001)
├── docs/
│   ├── architecture.png|pdf      # SUBMISSION: final architecture diagram
│   └── implementation_plan.md    # this file
├── reports/                      # generated outputs committed for submission
│   ├── data_quality_report.md    # SUBMISSION
│   ├── model_evaluation.md       # ours vs incumbent ptp_break score
│   ├── fairness_report.md
│   └── text_feature_eval.md
├── submission/
│   └── benchmark_answers.csv     # SUBMISSION: produced by scripts/run_benchmark.py, never hand-edited
├── data/                         # small, committed, human-produced inputs
│   ├── mappings/column_mappings.csv        # LLM-suggested, engineer-approved source→target mappings
│   ├── review/identity_decisions.csv       # human identity decisions, re-applied every run
│   └── labels/hand_labelled_notes.csv      # ~200 hand labels for text features
├── feature_repo/                 # Feast repo (see §5)
├── src/trustissues/
│   ├── common/                   # §3.0
│   ├── factory/                  # Layer 1 (§3)
│   ├── ask/                      # Layer 2 (§4)
│   ├── features/                 # Layer 3 (§5)
│   ├── nba/                      # Layer 4 (§6)
│   └── governance/               # cross-cutting (§7)
├── app/                          # Streamlit screens (§8)
├── scripts/                      # one-off and submission runners (§8)
└── tests/                        # §8
```

---

## 3. Layer 1: Data Product Factory (P1)

### 3.0 Shared plumbing: `src/trustissues/common/`

| File | Intention |
|---|---|
| `config.py` | Load `settings.yaml` / `roles.yaml`. Expose `AS_OF_DATE` and thresholds. |
| `db.py` | DuckDB connection factory. Creates the `raw`, `curated` and `gold` schemas. Read-only connections for Ask. |
| `llm.py` | Single LLM client: retries, parallel batching, response cache keyed by input hash, structured (JSON) output, prompt version recorded. |
| `asof.py` | Turns relative phrases ("last week", "this month", "last 90 days") into exact date literals from `AS_OF_DATE`. Used by Ask and by the features. |
| `sensitivity.py` | Loads `sensitivity.yaml`. Gives one source for protected, PII and proxy column lists, used by the decisioning view, the refusal gate, the SQL validator and the fairness tests. |
| `audit.py` | Appends to `gold.audit_log`: question/recommendation, sources, prompt, answer, model and prompt version, user role. |

### 3.1 Raw: `factory/ingest/`

| File | Intention |
|---|---|
| `load_raw.py` | Loads all 31 tables (CSV, Parquet by year, transcript JSON) into `raw.<table>` exactly as received. Stamps `_ingested_at`, `_source_file`, `_batch_id`, `_row_hash`. Skips unchanged rows by hash. |
| `manifest.py` | Per-file row count, column list and checksum, written to `raw._load_manifest`. Also checks every `voice_samples` file for existence, size and sha256 against the table; mismatches go to the DQ report. |
| `schema_drift.py` | Compares columns, types and enums with the contract and dictionary. A removed or retyped column stops the load. The LLM suggests a likely rename (e.g. `channel_v2`). |
| `dictionary.py` | Parses the data dictionary xlsx into `raw._dictionary`. Generates `config/sensitivity.yaml`, including the voice columns `customer_accent_region`, `agent_accent_region`, `agent_voice_id` and `customer_voice_id` as protected or proxy (kept out of the decisioning view and features). |

### 3.2 Curated: `factory/curate/`

| File | Intention |
|---|---|
| `standardise.py` | Pure functions, unit-tested: DOB → ISO (ambiguous day/month keeps both readings), phone → E.164 (masked phones keep their visible digits), name → uppercase tokens without accents, parsed with initials kept, postal → `A1A1A1` + FSA, employer normalisation. |
| `build_curated.py` | Runs the per-table SQL. Casts types (a failed cast becomes null, is flagged, and keeps its raw text). Dedupes on `_row_hash`. Picks the current CRM version and the latest `snapshot_date` per account (history kept separately). Validates enums against `code_lookups`. |
| `sql/<table>.sql` | One curated transform per source table. LLM-drafted from approved mappings, then reviewed. |
| `text_controls.py` | Marks notes and transcripts eligible for downstream LLM use: notes with `redaction_status = auto_redacted` that are within `retention_until`; transcripts with PII and PCI redaction applied. Recordings past their retention date are not treated as usable. |
| `mapping_assistant.py` | AI helper: proposes target field and transform per source column into `data/mappings/column_mappings.csv`, lowest confidence first, for the engineer to approve. |

### 3.3 Identity: `factory/identity/`

Matching passes, cheapest and surest first. When unsure, records stay separate.

| File | Intention |
|---|---|
| `nodes.py` | One node per source customer, not per account: `crm\|…`, `card\|…`, `loan\|…`, `dep\|…`, `bur\|…`. Stacked in one table with the same matching columns. |
| `rules.py` | Exact rule passes from the design doc: **national ID hash**; **name + DOB + phone**; **DOB + name + postal**. CRM dedupe also uses `duplicate_of_crm_id`. Phone alone never links. A conflict on a strong identifier blocks weaker rules (`blocked_conflict`). |
| `splink_model.py` | Splink setup. Blocking: DOB + FSA, DOB + last name, DOB + phone. Comparisons: name (Jaro-Winkler, initials), DOB (exact, swapped, ±1 year), phone (full or partial), postal, employer. EM training, then predict. Routing: ≥ 0.95 link, 0.80–0.95 grey zone, < 0.80 separate. |
| `second_opinion.py` | Grey-zone pairs serialised as tagged records (`[SRC] … [NAME] … [DOB] …`). The LLM returns match, probability, deciding fields and a reason. A pair links only if the LLM agrees with Splink. Any disagreement goes to the review queue. |
| `review_queue.py` | Writes `gold.identity_review_queue` with both explanations. Re-applies `data/review/identity_decisions.csv` on every run so the same pair is never asked twice. |
| `cluster.py` | Connected components over trusted links only. Conflict checks: two DOBs, two hashes, or more than 5 CRM records means no merge, and the cluster goes to review. Stable `G-000000` IDs reuse the oldest CRM record's ID. Unmatched records become singletons. Writes `gold.customer_xref` with method, confidence and status (`linked`, `in_review`, `unresolved`, `blocked_conflict`, `singleton`). |
| `relationships.py` | Joint holders and co-borrowers go into `gold.customer_relationship`. Never merged, never used as identity evidence. |

### 3.4 Gold and consumer views: `factory/gold/` (P1; `semantic_*` views are P2)

| File | Intention |
|---|---|
| `survivorship.py` | Precedence: verified value, then CRM, then most recent source. Bureau data is evidence only and never overwrites bank data. Each field gets `<field>_source` and `<field>_as_of`. |
| `build_c360.py` | Builds `gold.c360_customer` to the contract schema (§3.6). Safety flags are on if any source raises them. Consent takes the most restrictive value. Roll-ups come from accounts. Lineage is recorded. Also builds `gold.c360_account` (one row per account per snapshot, all products in one shape). |
| `views/customer_timeline.sql` | Contacts, promises, eligible redacted notes, transcripts (with `voice_sample_id` where a recording exists), offers and payments per golden ID, in time order. Used by customer RAG and the agent screen. |
| `views/decisioning_customer.sql` | **Allow-list** model view. Leaves out all 12 protected attributes, direct PII, geo coordinates, **age**, and the proxies accent and interpreter use. The feature store reads only this view. |
| `views/customer_safeguards.sql` | Hardship, vulnerability, accessibility and safety flags. Used by the rules engine, never for scoring. |
| `views/promises_enriched.sql` | One row per promise with golden ID and `ptp_created_ts`, as the point-in-time spine. |
| `views/semantic_*.sql` | One view per certified-metric shape for Ask, plus `bucket_transitions` for roll rate and a salary summary. |
| `publish.py` | Builds into a staging schema, runs the quality gate, then swaps it into `gold` only if every blocking rule passes. |

### 3.5 Quality: `factory/quality/` (P2; P1 supplies the contract YAML)

| File | Intention |
|---|---|
| `contract_checks.py` | Generates Great Expectations suites from `contracts/c360_customer.yaml`. Rules 1–5 block publishing; rules 6–8 warn (§3.6). |
| `anomaly.py` | Flags row counts and null rates more than 3σ off recent loads and matches them to `ops_events`. Explained anomalies are labelled; unexplained ones alert. |
| `dq_report.py` | Writes `reports/data_quality_report.md`: each issue found, records affected, how it was handled (duplicates, mixed date formats, mismatched IDs, schema drift, unmatched records, open review items). |

### 3.6 Orchestration and contract

- `factory/pipeline.py`: runs the nightly batch end to end (raw → curated → identity → gold → views → gate → publish). Times each stage against the ~45-minute target and records it in the DQ report.
- `contracts/c360_customer.yaml`: owner, grain, classification, refresh, freshness ≤ 24 h, schema, 8 quality rules, allowed and prohibited uses, all taken from the design doc's contract page. Schema fields as the design doc names them: `golden_customer_id`, `source_keys`, `match_method`, `match_confidence` (lowest link score), `full_name`, `date_of_birth`, `segment`, `province`, consent by channel, `cease_contact`, `deceased`, `bankruptcy`, `open_dispute`, `hardship_flag`, `total_balance`, `total_overdue`, `max_dpd`, `worst_bucket`, `active_promise_status`, `source_systems`, `refresh_ts`.

---

## 4. Layer 2: Insight and NLP, "Ask" (P2 structured, P4 RAG)

Pipeline order: **access check → injection classifier → refusal gate → as-of dates → router → path → validator → answer card**. No AI runs in the access check, refusal gate, date resolution or validator.

| File | Intention |
|---|---|
| `ask/service.py` | `ask(question, user) -> AnswerCard`. Runs the whole pipeline. The only function called by the app and the benchmark runner. |
| `ask/access.py` | Role scope: agents see their assigned cases (via `collections_cases` / `case_assignment_history`), team leads see aggregates, analysts see non-personal data. Produces the row filter and column mask the validator enforces. |
| `ask/guards/injection.py` | Small prompt-injection classifier (DeBERTa-v3-small ONNX per P2), with a heuristic fallback if the model isn't ready. |
| `ask/guards/refusal_gate.py` | Aho-Corasick over `refusal_lexicon.yaml` plus `sensitivity.py`. Whole-word matching. Refuses filtering or grouping by age ("ageing buckets" and "age of account" stay allowed), bulk personal data, and out-of-scope uses such as marketing. Always returns a reason and a safe alternative question. |
| `ask/router.py` | Classifies into the benchmark types (`metric`, `drivers`, `comparison`, `rag_customer`, `rag_corpus`, `guardrail`) and maps them to three paths: data, customer, policy. |
| `ask/answer_card.py` | Dataclass: understood filters (editable chips), SQL or sources, short summary, 3 follow-ups, `certified` / `uncertified` label, `not_found`, `refused` + reason. |

### 4.1 Data path: `ask/data/`

| File | Intention |
|---|---|
| `metric_catalog.py` | Loads `metric_definitions`. Embeds names, definitions and synonyms. Matches a question to a certified metric. |
| `metric_sql/<metric_id>.sql` | Hand-translated DuckDB template per certified metric (from the pseudo-SQL `formula`), with `default_filters` built in and named parameters. |
| `params.py` | The LLM fills only filters and dates (JSON schema per metric), then binds them into the certified template. |
| `drivers.py` | "What drove this?": splits the metric across approved dimensions only (product, segment, stated reason for paying late, payroll-delay band, prior broken promises) and ranks each one's contribution to the change. |
| `schema_graph.py` | networkx graph over gold tables, built from the dictionary's Relationships sheet plus `customer_xref`. Edges carry multiplicity; fan-out paths are penalised or routed through pre-aggregated views. Steiner tree for the join path. |
| `schema_linker.py` | Picks ~15 relevant columns out of 1,422 by matching question words and embeddings. Never indexes protected or PII columns. |
| `uncertified_sql.py` | Fallback: the LLM writes SQL over the pruned schema, labelled "uncertified". Re-prompted once on validator failure, otherwise says it can't answer reliably. |
| `validator.py` | sqlglot AST checks: read-only SELECT; allowed tables and columns only; joins on graph edges; metric default filters present; no PII or protected columns; role scope applied; `LIMIT` cap. |
| `execute.py` | Runs on a read-only DuckDB connection. Returns rows and latency. |

### 4.2 Customer and policy paths: `ask/rag/` (P4)

| File | Intention |
|---|---|
| `customer_path.py` | Long context, no vector search. Access check, then fetch `gold.customer_timeline` by golden ID (all CRM duplicates via xref). Only items dated on or before the as-of date and only eligible redacted text. Token budget filled newest-first (~20K). Answer states the date range covered. |
| `policy_ingest.py` | Chunks `reference_documents` markdown by section (sub-chunks never cross sections, with 1–2 sentence overlap). Stores metadata (doc type, language, confidentiality, products, provinces, effective date, version, section ID) into one Chroma collection plus a BM25 keyword index. |
| `policy_search.py` | Filters by role, confidentiality and **version in force on the as-of date** before searching. Hybrid search (vector + keyword), rerank (~20 → 5), expand each hit to its parent section, attach `hardship_programs` rows by SQL. |
| `answer.py` | Fixed answer structure: answer, quoted evidence, citation (doc ID, version, section, or note/transcript ID and date). Returns `not_found` instead of guessing. Implements the I6 contract (§7.1). |

---

## 5. Layer 3: Feature Store (P3, text features P4)

One definition per feature. The same SQL serves training (point-in-time at `ptp_created_ts`) and the morning scoring (the same files run as of `AS_OF_DATE`).

| File | Intention |
|---|---|
| `feature_repo/feature_store.yaml` | Feast config: file/DuckDB offline store (no online store). |
| `feature_repo/entities.py` | Entities: `golden_customer_id`, `ptp_id`, `account_id`. |
| `feature_repo/feature_views.py` | One feature view per group below. Sources are Parquet exports of the SQL in `features/sql/`. |
| `features/sql/delinquency.sql` | Days past due now and worst in the last 12 months. |
| `features/sql/promise_history.sql` | Promises broken in the last 90 days. |
| `features/sql/payday.sql` | Gap between due date and payday; how late salary is (`salary_credit_history`). |
| `features/sql/liquidity.sql` | Card utilisation; deposit balance vs promise amount. |
| `features/sql/bureau.sql` | Change in bureau score over 90 days (normalised by `risk_score_model` range). |
| `features/sql/channel_response.sql` | Response rate by channel, last 30 days. |
| `features/text_extract.py` | LLM over redacted notes and transcripts. Outputs hardship signal, stated reason for paying late, promise strength, dispute mentioned. **Each signal is stored with its evidence sentence**, `source_id`, `source_ts`, `prompt_version`. Extracted once per note and cached. Scope it to customers in the training window and open cases to keep LLM cost bounded. |
| `features/text_eval.py` | Scores extraction against `data/labels/hand_labelled_notes.csv` (~200) and the release's public labels. Writes `reports/text_feature_eval.md`. Text features are gated on passing this. |
| `features/asr_check.py` | **Stretch (P4).** Transcribes ~50 recordings with local Whisper and writes the word error rate against the JSON transcripts into `reports/text_feature_eval.md`. |
| `features/build_training_set.py` | Point-in-time join on `gold.promises_enriched` using Feast historical retrieval. Label = promise broken (status `broken`, or `partially_kept` per metric definition). |

---

## 6. Layer 4: Models and Decisioning, Next Best Action (P3)

Rules first, then model, then action choice, then explanation, then human review.

| File | Intention |
|---|---|
| `nba/rules.py` | Hard stops (cease contact, deceased, bankruptcy, open dispute → no contact). Consented channels only. Contact limits and permitted hours. Returns the allowed actions plus which rule blocked each removed action. **No AI.** |
| `nba/hardship.py` | Any hardship or vulnerability sign routes the case to a certified agent (`agents.vulnerable_customer_certified`). Programme eligibility comes from SQL over `hardship_programs`, valid on the as-of date. **No AI.** |
| `nba/train.py` | LightGBM on the point-in-time training set. Time-based split (train on earlier months, test on later ones). Calibrated. Saves the model and its feature list with a version. |
| `nba/evaluate.py` | Compares with the incumbent `model_scores` ptp_break score on the same promises: AUC, calibration, share of broken promises caught in the riskiest 20%. Writes `reports/model_evaluation.md`. |
| `nba/fairness.py` | Compares outcomes across protected groups, joined back **only here**, for testing. Writes `reports/fairness_report.md`. |
| `nba/actions.py` | Action catalogue, including wait. Values each allowed action as expected payment minus contact cost, using the bank's past test-cell results and `channel_capacity.unit_cost_cad`. Hardship can only make treatment gentler. |
| `nba/allocate.py` | **Stretch (first cut).** Fits the chosen actions to the day's agent capacity (`agents`, `agent_shifts`) and channel capacity (`channel_capacity`). |
| `nba/explain.py` | SHAP top 3 reasons, why the action beat the runner-up, which rules ruled out others, and the evidence note or call behind any text signal. Template text, e.g. "High risk of breaking (0.81): …". |
| `nba/reword.py` | **Stretch (cut first).** Optional LLM rewording, then a fact check (numbers, reasons and dates must match the template). Falls back to the template on mismatch. |
| `nba/queue.py` | Nightly morning queue into `gold.nba_decisions` (decision card JSON, model version, feature snapshot ID). |
| `nba/review.py` | Accept or override with a reason code (I7). Team-lead approval of the daily digital-reminder batch. Mandatory approval for hardship, vulnerability and high-balance cases. Writes `gold.nba_overrides`. |
| `nba/promise_coach.py` | **Stretch.** Break risk for alternative dates and amounts during a call, from the model, no LLM, under 100 ms (would need an online store again). |

---

## 7. Governance and interfaces

### 7.1 Interfaces between owners

| ID | From → To | Shape |
|---|---|---|
| I1 | P1 → P3 | `gold.decisioning_customer`, `gold.promises_enriched`, `gold.customer_safeguards` |
| I2 | P1 → P2 | `gold.semantic_*`, `gold.bucket_transitions`, schema graph inputs |
| I3 | P4 → P3 | `gold.text_features {golden_customer_id, source_id, source_ts, hardship_signal, stated_delay_reason, ptp_intent_strength, dispute_mentioned, evidence_sentence, confidence, prompt_version}` |
| I6 | P2 router ↔ P4 RAG | in `{question, question_type, golden_customer_id?, user_role, as_of_date}` → out `{answer, evidence[], citations[], confidence, not_found, coverage}` |
| I7 | App → P3 | `gold.nba_overrides {recommendation_id, agent_id, decision, reason_code, ts}` |
| — | P3 → P2 | `gold.nba_decisions` added to the Ask schema graph |

Freeze these shapes in the first 4 hours, as stub tables with the right columns, so every owner can build against them in parallel.

### 7.2 `src/trustissues/governance/`

| File | Intention |
|---|---|
| `roles.py` (P4) | Resolves a demo user (agent ID / team lead / analyst) to a scope. Shared by Ask, RAG and the app. |
| `masking.py` (P2) | Masks PII by default in any dataframe shown in the UI unless the role allows it. |
| `monitoring.py` (P3) | Summaries for the DQ page: override rate, model AUC/PSI, LLM feature accuracy, open identity reviews. |

---

## 8. App, scripts, tests

**`app/`** (Streamlit; owner per page in the Owner column)

| File | Owner | Intention |
|---|---|---|
| `Home.py` | P4 | Role picker (agent / team lead / analyst) and a short pipeline status. |
| `pages/1_Ask.py` | P4 | Question box, answer card with editable filter chips, a "show the working" drawer (SQL or sources), summary, follow-ups, refusal display. |
| `pages/2_Agent_Queue.py` | P3 | Morning queue for the selected agent, sorted by value. |
| `pages/3_Decision_Card.py` | P3 | C360 summary, timeline, recommendation, top reasons, blocked actions, evidence quotes, and accept/override with a reason code. Optional audio player for the call recording (polish). |
| `pages/4_Team_Lead.py` | P2 | Approve the digital-reminder batch and the mandatory-approval cases; weekly override review. |
| `pages/5_Identity_Review.py` | P2 | Grey-zone and conflict pairs side by side with Splink and LLM reasons. Decisions append to `identity_decisions.csv`. |
| `pages/6_Data_Quality.py` | P2 | Contract rule results, anomalies, match-status counts. |

**`scripts/`**

| File | Intention |
|---|---|
| `download_data.sh` | `hf download nuxsh/maple-collections-hackathon` and unzip both zips into `maple_data/`, then move the voice pack's `files/voice/` into `maple_collections_release/files/` so `voice_samples.file_path` resolves. |
| `run_benchmark.py` | Runs `ask.service.ask` on every benchmark question (plus the 7 PM extras) at each question's `as_of_date`. Writes `submission/benchmark_answers.csv` (`question_id, answer, sql_or_sources, refused`) in the template format. Reports a dev score against the dev answers file. Run it after every Ask change. |
| `label_notes.py` | Small CLI for hand-labelling ~200 notes. |
| `nightly.py` | Pipeline, then text features, then features, then scoring, then queue. Used for the demo "morning" run. |

**`tests/`** (prioritise the governance guarantees)

- `test_standardise.py`: DOB, phone, name and postal edge cases.
- `test_identity_rules.py`: phone alone never links; joint holders never merge; a conflict blocks weaker rules; cluster conflict checks.
- `test_decisioning_view.py`: no protected, PII or proxy columns (contract rule 4).
- `test_refusal_gate.py`: protected attributes, bulk PII and marketing are refused; "average" and "ageing buckets" are allowed.
- `test_validator.py`: writes rejected, PII columns rejected, missing default filters rejected.
- `test_asof.py`: relative dates resolve from 2026-09-28.
- `test_hardship_monotonic.py`: a hardship signal never makes the recommended action harsher.
- `test_rules_hard_stops.py`: no contact action survives cease, deceased, bankruptcy or dispute.
- `test_feast_offline.py`: Feast's point-in-time retrieval returns the computed feature values.
- `test_card_fact_check.py`: reworded cards with changed facts fall back to the template.

---

## 9. Conflicts resolved

| Topic | Person doc said | Design doc (used) |
|---|---|---|
| Layer count | P3: Layer 3 = AI Decisioning | 4 layers: L3 Feature Store (Feast, Redis), L4 Models and Decisioning |
| Rule passes | P1: tiers 0–4 incl. bureau↔deposit CIF and DOB + phone + compatible name | Only national ID hash; name + DOB + phone; DOB + name + postal. Other cases go to Splink. Add the P1 tier 0 rule only if the CIF link is confirmed on real data, and record it in the README "what changed" section. |
| Quality rule 3 | P1: no link below 0.80 | No automatic link below 0.95 unless confirmed by the LLM or a person |
| Quality rules | P1: 9 (incl. bureau score range) | 8 rules as on the contract page. Bureau range can sit in the DQ report as a non-contract check. |
| Contract field names | P1: `province_code`, `max_dpd_current`, `active_ptp_status`, + extras | `province`, `max_dpd`, `active_promise_status` |
| Age | P1: keep age as a proxy-watch column | Age left out of the decisioning view entirely, with accent and interpreter use |
| Batch time | P1: ~42 min | ~45 min |
| Data-question latency | P2: 0.65–1.3 s | 1–2 s |
| Promise coach | P3: core, 20–60 ms | Stretch, under 100 ms, no LLM |
| Event re-scoring | P3: core | Stretch |
| French | P4: search EN + FR, answer in the user's language | Stretch. Index EN first. |
| Hard stops | P3: + third-party representation, statutory holidays, language | Cease contact, deceased, bankruptcy, open dispute, consent, contact limits, permitted hours. P3 extras only if time allows. |
| Mandatory approval | P3: + near-threshold cases | Hardship, vulnerability, high-balance |
| Lift estimate | P3: off-policy evaluation on test cells | Not in design; skip unless time allows |
| Voice | WAV files available | Recordings are loaded and checksum-verified as data. No transcription in the core build. A speech-to-text agreement check is a stretch item. |

---

## 10. Schedule (maps the design doc's build plan to files)

**Hours 0–4 are shared.** All four agree the stub tables (§7.1), get `load_raw` and `llm.py` working, and share one DuckDB. P1 delivers `curated.promises` and related tables first, ahead of identity, so P3 is not blocked.

| Hours | P1 | P2 | P3 | P4 |
|---|---|---|---|---|
| 0–4 | `load_raw`, `dictionary`, `sensitivity.yaml`, interface stubs, then curated promises/accounts first | `metric_catalog`, `metric_sql/*`, `refusal_gate`, `run_benchmark.py` skeleton | `build_training_set` on curated promises, Feast repo skeleton | `llm.py`, `audit.py`, `text_extract` prompt, `label_notes.py` + ~200 labels |
| 4–12 | `standardise`, `build_curated`, `rules`, `splink_model` | `params`, `execute`, `access`, `asof`, `semantic_*` views | `features/sql/*`, Feast repo | `policy_ingest`, `policy_search`, `text_extract` run |
| 12–20 | `second_opinion`, `cluster`, `build_c360`, `decisioning_customer`, `publish` | `validator`, `drivers`, `schema_graph`, `uncertified_sql`; start `contract_checks` | `train`, `evaluate`, `text_eval` hookup | `customer_path`, `answer`, Home + Ask pages, `roles.py` |
| 20–28 | `review_queue`, `c360_customer.yaml`, `pipeline.py`, fix identity issues | `anomaly`, `dq_report`, benchmark runs and fixes, Identity Review page | `rules`, `hardship`, `actions`, `explain`, `queue`, `review`, Agent Queue + Decision Card pages | `text_eval`, RAG fixes, benchmark RAG questions, architecture diagram |
| 28–36 | Timings, README "what changed", help P2/P4 | Team Lead + Data Quality pages, `masking.py`, extra questions at 19:00 on 4 Oct, final CSV | `fairness`, `monitoring.py`, then `allocate` / `reword` only if time | Demo video, deck, README, submit by 21:00 |

**Cross-help rule:** whoever finishes a block early takes the next unowned item from the slowest owner. Expect P1 (identity) to need it most.

**Stretch, in order:** promise coach → event re-scoring → French policy answers → voice speech-to-text check.

---

## 11. Verify once the data is loaded

- Whether bureau `bank_subject_ref` really equals deposit `src_customer_ref` (decides the extra rule pass).
- Meaning of `redaction_status = none` and how many notes it covers (excluded until confirmed).
- Transcript retention source (`contact_history.recording_retention_until` via `contact_id`?).
- How `hardship_programs.policy_section` joins to document sections.
- Where the bank's past test-cell results live, for action valuation (champion/challenger fields in cases, contacts or `offers`).
- All 2,000 voice recordings match their `voice_samples` checksums, and every `voice_samples.transcript_id` exists in `call_transcripts`.
- Release README: file conventions, answers template, dev answers, the `DC-COLL-001` contract example, the public note and transcript labels.
