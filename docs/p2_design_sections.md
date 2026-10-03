# P2 Design Sections: For Final System Design PDF

All numbers were measured on the development data (`warehouse/ask_dev`, 3 October 2026, one machine, warm and sequential unless stated). The development data holds 367,229 cases, 2,688,337 contacts, 238,815 promises, 1,064,500 account rows and 12,569,692 month-end snapshot rows. Two things are empty in the development data: `gold.customer_xref` and `gold.semantic_payroll` have no rows, and no development case carries a golden customer ID. Anything that needs identity or payroll (the payroll-delay metric, customer-level joins) cannot be shown on this data. Design intent is marked as such.

## Heading: Layer 2: Insight and NLP Platform (Structured)

### 1. Architectural Overview & Query Pipeline

Layer 2 answers collections questions in plain language over the curated data. Most numbers do not come from a model. The 17 metrics in the metric catalogue each have a reviewed SQL template, and a question that names one is answered by filling that template with a period and a breakdown. A model writes SQL only for questions no metric covers, and that answer is labelled uncertified. Every statement, from either source, passes the same validator before it runs.

A question goes through these stages in order. The two model-backed stages (3, and the fallback in 4) run only when a language-model client is connected; without one, the rules alone decide.

1. **Prompt-injection screen (rules).** Regular expressions look for attempts to override the assistant's instructions, reveal its prompt, or smuggle SQL (for example `; DROP TABLE`), plus patterns that pair an override or reveal verb with an object such as "instructions", "rules" or "configuration". A hit refuses the question and nothing else runs. This is a heuristic, not a trained classifier.
2. **Refusal gate (rules).** A lexicon refuses requests to select, rank, treat or describe customers by a protected attribute or a proxy for one, to export personal identities or contact details in bulk, and out-of-scope topics. Protected and proxy phrases are generated from the sensitivity registry (`config/sensitivity.yaml`) and searched in one pass with an Aho-Corasick automaton (`pyahocorasick`, whole-word matching), alongside hand-written context rules. The context rules separate a person's age from the age of an account, and statistics about phone or email contacts from a request for phone numbers, so "average days past due" and "contact rate by channel" are not refused. Without `pyahocorasick`, the same phrases are matched with boundary-aware regular expressions and the refusal results are unchanged.
3. **Model safety screen.** The model is asked whether the question clearly uses a protected attribute or a proxy such as a name or origin, asks for identities or contact details in bulk, or tries to override or reveal the assistant's instructions. **It can only add a refusal.** A rule refusal is final and never reaches it, and a model failure, a timeout or an unusable reply leaves the rules' verdict as it was. The prompt tells it to allow a question when unsure, so a request that no rule and no model reading catches passes. It sees only the question text.
4. **Router.** The router chooses one of seven outcomes: `metric`, `drivers`, `rag_corpus`, `rag_customer`, `sql`, `incident` or `cannot_answer`.
   - Deterministic metric triggers are tried first. A case ID with call wording goes to `rag_customer` only when no metric trigger matched.
   - Wording that asks what a policy requires goes to `rag_corpus`; "why did X change" goes to `drivers`; a case population count goes to `sql`.
   - If no trigger matches, a local embedding matcher compares the question with each metric's name, definition, synonyms and example (`BAAI/bge-small-en-v1.5` on CPU through FastEmbed/ONNX; no question leaves the machine). A metric is selected only if its similarity is at least 0.65 and it leads the next metric by at least 0.055; these are thresholds, not calibrated probabilities. If two metrics are close, the router asks which one is meant.
   - When the result is `cannot_answer`, including a pending clarification, the **model fallback** asks the model to choose a path and, for a metric, one catalogue id. So a clarification can be replaced by a model choice. It sees the question and the metric definitions only; an unknown id, an unknown path or a wrongly typed reply is ignored. A chosen metric still goes through the parameter builder.
   - A recognised breakdown the metric does not support (for example "by individual customer") returns "unsupported" or a refusal, not a nearby number. Recognition is by wording, so an unfamiliar unsupported filter can still be lost (see limits).
5. **Access check.** The role decides which paths are open: metric, SQL and drivers to team leads and analysts; policy questions to every role; customer-call questions to agents (assigned cases only) and team leads. The path is checked before the case, and a missing case and a case the user may not see give the same message, so the answer does not reveal which cases exist.
6. **Date resolution.** Named and relative periods ("September 2026", "the 12 months to 28 September", "end of March", "today") are resolved in code against the as-of date supplied with each call (configured as 28 September 2026). Periods are half-open and clamped to the as-of date. On the generated-SQL path, a question about a period that starts after the as-of date gets a note saying so.
7. **Answer paths.**
   - **Certified metric.** A reviewed template (17 metrics plus a cohort variant of the cure rate) is filled with the parsed period and breakdown. Most rates report the numerator and denominator beside the rate; the out-of-hours share reports the percentage and the number of calls.
   - **Drivers ("why did it change").** The certified metric is computed for two periods and the change is split into a rate effect and a mix effect across approved dimensions; counts are split by segment. Protected attributes, direct PII and experiment labels are never split dimensions. For rates, a change under 2% is called small; for call volume, a change spread evenly across segments is reported as broad-based. The answer states which segments moved and does not claim a cause.
   - **Policy and regulation questions** go to P4's policy RAG (51 policy chunks) through the shared model wrapper, with a required supporting quotation and citation. A quotation does not prove every sentence of the answer; the text answers are still reviewed by a person.
   - **Customer-call questions** go to P4's customer path (`ask/rag/customer_path.answer_customer`) through `make_rag_handler(index, llm, con=...)`, connected in the P2 merge (4 Oct). The case is resolved with `access.case_customer_resolver` (`gold.case_customer` and the agent assigned on the as-of date); P4 re-checks the customer and its refusal reads the same as a missing case. With no resolver the answer is "customer lookups are unavailable". The contract, and the open question of whether a named case limits the answer to that case, are in `docs/interfaces.md`.
   - **Generated SQL fallback.** For questions no metric covers (for example "open cases by bucket"), the model writes one DuckDB SELECT over the six `gold.semantic_*` views. The prompt lists all 73 approved columns with notes, plus join hints (a Steiner path over the approved join keys, and warnings about fan-out and snapshot pairing). The hints are text only; the validator is the control.
8. **Validation and execution.** Every statement is parsed with `sqlglot` and must satisfy: read-only SELECT, one statement, no set operations, approved tables, columns and join keys only, no direct-PII or protected columns for the role, and trusted row filters inside each scan, with a row cap of 500 on returned rows. Generated queries receive the as-of limit in addition to the role's own filters. Certified queries pass explicit as-of parameters instead. Execution has a 5-second timeout per statement, set on the execution only, not on validation, model calls or repairs. Before a generated query runs, three further checks apply, all heuristic: a filter on a label column (queue, bucket, outcome, product, channel and similar) must use a value that exists for that user (found through the same validated policy, and skipped when the lookup cannot run); a question that says "calls", "SMS", "email", "outbound" or "inbound" must have the matching channel or direction as a top-level AND term of a WHERE clause; and an unknown column triggers a repair hint naming the table that owns it. A query is repaired once and then rejected with "could not answer reliably". Two other reads sit outside this validator: the case resolver and the policy retrieval query their own tables directly.
9. **Answer card.** A card carries the question, the answer, the path, the as-of date, the SQL and its parameters, the sources, a certified flag and notes. On a successful answer the SQL is the statement that ran. A failure card can carry the rejected statement instead. Parameters are stored as parsed values and shown with ISO dates in the benchmark file and the drivers log. Grouped rows are sorted in a natural order (`1-30` before `31-60`) so a repeated question returns the same table, except where a generated query supplies its own ORDER BY, which is kept.

What reaches the model: the safety screen and the router fallback see the question text and metric definitions only. The SQL prompt carries schema text and the question. Policy answers carry policy chunks, which include hardship-programme records. A repair message can carry label values the user is allowed to see, and error text with numbers removed.

### 2. What the Answer Card Contains (the screen is not built)

The record holds what a screen needs: parsed parameters, the executed SQL, the sources, the certified flag and the notes ("uncertified", "one repair", "period after the as-of date"). The editable parameter chips, the working drawer and the suggested follow-up questions are design intent. No Ask screen exists.

### 3. Evidence of Quality

| Check | Result |
|---|---|
| Released questions with a development grade (21 of the 35), full pipeline with `--llm` | 14 pass, 6 text answers marked for human review, 1 fail. The other 14 released questions have no grade, and "review" means a person has not yet confirmed the answer. The 5 expected refusals are refused and none of the other 30 is. |
| The one failure (BQ-017, roll rate) | The reference answer's date parser drops month-first dates; an independent calculation from the raw files with the corrected parser matches our curated result (auto loan 8.1545% on 466, against the reference's 7.4419% on 430). The team is handling it. |
| Reproducibility | Five full runs of the 35 questions (`eval/repro_runs/`). The two runs after the ordering fix that bypassed the cache, and one cached run, give identical numeric answers. Policy text answers vary in wording across runs (BQ-001, BQ-019) while the facts match. Two earlier runs differed only in the order of grouped rows, which is now fixed. |
| Router paraphrase sets (current scores, with the model fallback and safety screen) | 100% on the 72-case set the rules were tuned on; 94.5% on a 55-case held-out set; 94.9% on a 39-case fresh set; 95.2%, 95.8% and 95.5% on three later safety-focused sets. Cached model responses were used, so these reproduce earlier model outputs. The later sets were used to diagnose misses, so they overstate unseen accuracy. |
| First scores on unseen wording | Rules only: 47.2% on the original set, 47.3% on the held-out set. With the model fallback added: 80.0% on the held-out set and 84.6% on the fresh set. On refusals, 4 of 16 on the first safety set with rules only, then 16 of 16 once the model screen was added. A final 22-case set scored once with the screen on: 12 of 12 refusals and 6 of 6 look-alike questions passed; one policy question about elderly customers was wrongly refused. |
| Optional packages (router scores by configuration) | With everything installed: 100 / 94.5 / 94.9 / 95.2 / 95.8 / 95.5%. Without `pyahocorasick`: identical. Without FastEmbed (embedding matcher) and with `--llm`: 66.7 / 63.6 / 56.4 / 85.7 / 91.7 / 95.5%. Without FastEmbed and without a model: 66.7 / 49.1 / 41.0 / 85.7 / 41.7 / 68.2%. FastEmbed and its model files are needed to reproduce the full scores (`requirements-router.txt`, `scripts/setup_router.py`). |
| Generated SQL | 30 unseen questions, each checked against a reference query written independently (`eval/sql_stress/`). After the fixes, all 30 return correct values under a defensible reading. Five differ from the reference in layout, units or a definition choice (cure date versus cure outcome). Before the fixes, one question counted all outbound contacts as calls; that class is now blocked for wording the check knows. |
| Adversarial probes | `DELETE`, `UPDATE` and `DROP TABLE` text never ran. Bulk identity pulls were refused or rejected by the validator. A filter on a label that does not exist (`reported_bucket = 'default'`) returned a confident 0 before the label check; it now says the value does not exist. |
| Automated tests | 655 |

### 4. Known Limits

- **Safety layers are heuristics.** The rules and the model screen both pass what they do not recognise; the screen is told to allow when unsure, and a model failure leaves the rules' decision. Each layer can miss a request, and the screen can refuse a legitimate one (seen once).
- **Certified does not mean correctly interpreted.** A certified card shows a reviewed template ran. It does not show the template answers the question as asked. Open cases: "touches per open case" can select the average-days-past-due metric; a request for a historical period the data cannot support (delinquency in August 2025) is answered at the as-of snapshot; a filter to one case on a portfolio metric is dropped. These are in the catalogue parser and are on the fix list.
- **Generated SQL is tested on 30 questions**, mostly single-table or simple joins, and every answer is uncertified. The channel, direction and label checks are heuristics: a query can satisfy them and still use the wrong definition. "Late stage" can be read as the late-stage queue or the last two delinquency buckets; the model is asked to state how it read an ambiguous word, and the card shows it as an "Interpretation" note, but the note is the model's own sentence and is not checked. The 500-row cap limits returned rows, not rows scanned.
- **Reads outside the validator.** The case resolver and the policy retrieval query their tables directly.
- **Certified history scans.** The roll-rate template joins the 12.6 million-row snapshot view twice.
- **Concurrency is unmeasured.** Execution is serialised by a lock. The latency figures are single-user.
- **Customer-call questions** wait for P4's function. One metric variant (BQ-014) is unsupported.
- **Model-backed stages need the provider key at run time** (the configured model is the free tier of Nemotron 3 Super on OpenRouter, which is rate-limited), and the embedding matcher needs its model files (`fastembed` is in the main requirements; run `scripts/setup_router.py` once to download them).
- **Not built:** the Ask screens, small-group disclosure limits for tiny breakdowns, the DeBERTa classifier, a compressed Radix Trie and a learned schema pruner that earlier drafts described.

Small-group disclosure, listed above, is now implemented for grouped metric, drivers and SQL cards with usable count columns. `config/disclosure.yaml` defaults to a note for counts below five; optional suppression nulls numeric cells in affected rows. Single-row totals and results without counts are not checked, and check failures leave answers unchanged. Note mode warns; it does not prevent disclosure.

---

## Heading: Scalability, Part 2: Real-Time / Query Serving (merge with P1's part under one "Scalability" heading)

### 1. Measured Latency (development data, one machine, single user)

Where two measurements exist, both are shown.

| Stage | Technology | Measured |
|---|---|---|
| Injection screen and refusal gate (rules, Aho-Corasick) | regular expressions, pyahocorasick | 0.14–0.20 ms |
| Router trigger rules | regular expressions | 0.11–0.25 ms |
| Metric matcher, uncached | bge-small ONNX on CPU | 10.7–13.7 ms median; first call after start 1.8 s to load the model |
| Join hint for the SQL prompt | networkx over approved join keys | 0.7–1.0 ms |
| Validation of one statement | sqlglot | 70–165 ms, depending on the query |
| Certified metric, end to end, no model | template + DuckDB | 120–270 ms |
| Drivers, end to end, no model | several certified queries | 0.8–1.3 s |
| Model safety screen, per question, uncached | hosted Nemotron via OpenRouter | 1.3–2.5 s |
| Model router fallback, uncached (only when rules do not place a question) | hosted Nemotron | 1.1–2.2 s |
| Certified metric with an uncached screen | screen + template + DuckDB | 1.5–2.5 s |
| Certified metric with a cached screen | cache + template + DuckDB | 0.22 s |
| Generated SQL, end to end, uncached | screen + generation + validation + execution | 3.3–7.8 s |

With the model screen connected, every question makes one language-model call. The `--no-cache` flag on the benchmark runner makes every call fresh. A full uncached run of the 35 released questions took 99 seconds (one measurement). Earlier drafts estimated 0.65–1.3 s to the first result, built on a 12 ms classifier and 600–1,200 ms generation; that was never measured and does not hold with the screen on.

### 2. Serving Architecture

- **Embedded, read-only DuckDB.** A file-backed query opens the database read-only. There is no server to run. Questions reach the gold views; the validator adds trusted filters inside each scan for generated SQL.
- **Large history tables.** The generated-SQL path is the main risk for scans, bounded by the 500-row output cap and the per-statement 5-second timeout. The certified roll-rate template also scans snapshot history.
- **Response cache.** The model wrapper caches successful responses keyed by the whole request (model, prompts, settings, prompt version), so repeated questions and reruns are free and reproducible.
- **Concurrency is untested**, and execution is currently serialised by a lock.

---

## README entry: what changed from the design

| Earlier draft | What is built |
|---|---|
| DeBERTa-v3-small (ONNX) injection classifier, about 12 ms | Rule patterns plus a hosted-model safety screen that can only add refusals. No DeBERTa model was trained or shipped. |
| Aho-Corasick automaton compiled from a Radix Trie, about 2 ms | `pyahocorasick` over phrases generated from the sensitivity registry, with a regex fallback; no separate Radix Trie. The rules together take 0.14–0.20 ms. |
| Embedding matcher (all-MiniLM) for metric selection | FastEmbed/ONNX `bge-small-en-v1.5` on CPU with similarity and margin thresholds, behind trigger rules, with a hosted-model fallback for what remains. |
| Steiner-tree pruning to about 15 columns, validator enforcing the tree | Steiner path used as text hints in the SQL prompt only. The prompt lists all 73 approved columns. The validator enforces approved join keys, not the tree. |
| Router with six categories including `comparison` and `guardrail` | Seven outcomes: `metric`, `drivers`, `rag_corpus`, `rag_customer`, `sql`, `incident`, `cannot_answer`. Comparisons are metrics or drivers; guardrails are the refusal gate and screens before routing. |
| The model extracts operational parameters into the certified formula | Parameters of a certified metric are parsed in code. The model never supplies one; it may only choose which catalogue metric. |
| Editable parameter chips, working drawer, follow-up suggestions | Answer record only; no screen built. |
| Latency estimate 0.65–1.3 s to first result | Measured: about 1.5–2.5 s uncached for a certified metric with the safety screen on, 0.22 s when cached. |
| Row cap `LIMIT 100` | Row cap 500 on returned rows; 5-second timeout per statement. |
| (not in the earlier draft) | Added during the build: model fallback and model safety screen (additive only), label-value and channel/direction checks for generated SQL, stable row ordering, response cache and `--no-cache`, held-out evaluation sets, reproducibility run records. |
