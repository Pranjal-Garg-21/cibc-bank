# P2 Design Sections: For Final System Design PDF

All numbers below were measured on the development data (`warehouse/ask_dev`, 3 October 2026, one machine, median of repeated runs). The development data holds 367,229 cases, 2,688,337 contacts, 238,815 promises, 1,064,500 account rows and 12,569,692 month-end snapshot rows. Where something is a design intent and not a measurement, it is marked.

## Heading: Layer 2: Insight and NLP Platform (Structured)

### 1. Architectural Overview & Query Pipeline

Layer 2 answers collections questions in plain language over the curated data. It does not let a model write most of the numbers. The 17 metrics in the metric catalogue each have a reviewed SQL template, and a question that names one is answered by filling that template with a period and a breakdown. A model writes SQL only for questions no metric covers, and that answer is labelled uncertified. Every query, from either source, passes the same validator before it runs.

A question goes through these stages in order:

1. **Prompt-injection screen (rules).** A set of regular expressions looks for attempts to override the assistant's instructions, reveal its prompt, or smuggle SQL (for example `; DROP TABLE`), plus structural patterns that pair an override or reveal verb with an object such as "instructions", "rules" or "configuration". A hit refuses the question and nothing else runs. This is a heuristic screen. It is not a trained classifier.
2. **Refusal gate (rules).** A lexicon refuses requests to select, rank, treat or describe customers by a protected attribute or a proxy for one, to export personal identities or contact details in bulk, and out-of-scope topics. Protected and proxy phrases are generated from the sensitivity registry (`config/sensitivity.yaml`) and searched in one pass with an Aho-Corasick automaton (`pyahocorasick`, whole-word matching). Context rules separate a person's age from the age of an account, and statistics about phone or email contacts from a request for phone numbers, so "average days past due" and "contact rate by channel" are not refused. If `pyahocorasick` is not installed, the same phrases are matched with boundary-aware regular expressions, so refusals do not depend on the optional package.
3. **Model safety screen.** A second layer asks the language model whether the question clearly uses a protected attribute or a proxy such as a name or origin, asks for identities or contact details in bulk, or tries to override or reveal the assistant's instructions. **This layer can only add a refusal.** A rule refusal is final and never reaches it, and a model failure, a timeout or a malformed reply leaves the rules' verdict unchanged. It sees only the question text, never data.
4. **Router.** The router chooses one of: `metric`, `drivers`, `rag_corpus`, `rag_customer`, `sql`, `incident`, or `cannot_answer`. It works in this order:
   - A case ID plus call wording goes to `rag_customer`.
   - Wording that asks what a policy requires goes to `rag_corpus`.
   - Deterministic trigger phrases per metric are checked first. If none matches, a local embedding matcher compares the question with each metric's name, definition and synonyms (`BAAI/bge-small-en-v1.5`, run on CPU through FastEmbed/ONNX, no question leaves the machine). A metric is selected only if its similarity is at least 0.65 and it leads the next metric by at least 0.055. These are similarity and margin thresholds, not calibrated probabilities. If two metrics are close, the answer asks which one is meant.
   - "Why did X change" goes to `drivers`.
   - If the rules and the matcher still do not place the question, the **model fallback** asks the language model to pick a path and, for a metric, one id from the catalogue. It sees the question and the metric definitions only. An unknown id, an unknown path or a wrongly typed reply is ignored. A chosen metric still goes through the parameter builder, so an unsupported period or breakdown is still rejected.
   - A breakdown the metric does not support (for example "by individual customer") returns "unsupported", never a nearby number.
5. **Access check.** The user's role decides which paths are open: metric, SQL and drivers to team leads and analysts; policy questions to every role; customer-call questions to agents (assigned cases only) and team leads. A missing case and a case the user may not see give the same message, so the answer does not reveal which cases exist.
6. **Date resolution.** Relative and named periods ("September 2026", "the 12 months to 28 September", "end of March", "today") are resolved in code against the fixed as-of date, 28 September 2026. Periods are half-open and are never extended beyond the as-of date, because no data can exist after it. On the generated-SQL path, a question about a period that starts after the as-of date gets a note saying so.
7. **Answer paths.**
   - **Certified metric.** A reviewed template (17 metrics, plus a cohort variant of the cure rate) is filled with the parsed period and breakdown. The answer reports the numerator and denominator beside the rate.
   - **Drivers ("why did it change").** The certified metric is computed for the two periods and the change is split exactly into a rate effect and a mix effect across approved dimensions only. Protected attributes, direct PII and experiment labels are never split dimensions. Counts are split by segment. If the change is spread evenly across segments, or smaller than 2%, the answer says so. It reports which segments moved and does not state a cause.
   - **Policy and regulation questions** go to the policy RAG built by P4 (51 policy chunks) through the shared language-model wrapper. The answer is grounded in retrieved chunks and cited.
   - **Customer-call questions** use P4's customer function. It is not connected yet, so these return "not connected yet" (the contract is written in `docs/interfaces.md`).
   - **Generated SQL fallback.** For questions no metric covers (for example "open cases by bucket"), the model writes one DuckDB SELECT over the six `gold.semantic_*` views. The prompt carries the table notes and approximate join hints (a Steiner path over the approved join keys, plus warnings about fan-out and snapshot pairing). The hints are text only. The validator is the control.
8. **Validation and execution.** Every statement is parsed with `sqlglot` and must satisfy: read-only SELECT, one statement, no set operations, approved tables and columns only, approved join keys only, no direct-PII or protected columns for the role, trusted row filters added inside each scan (the as-of limit and the role's scope), a row cap of 500 and a 5-second execution timeout. Before a generated query runs, three more checks apply: a filter on a label column (queue, bucket, outcome, product, channel and similar) must use a value that exists for that user, found through the same validated policy; a question that says "calls", "SMS", "email", "outbound" or "inbound" must filter on that channel or direction; and an unknown column triggers a repair hint naming the table that owns it. The query is repaired once with the error and then rejected. A rejected query returns "could not answer reliably", not a guess.
9. **Answer card.** Each answer carries the question, the answer, the path, the as-of date, the executed SQL and its parameters (dates in ISO form), the sources, a certified flag and notes. Grouped rows are sorted in a fixed natural order (`1-30` before `31-60`), so the same question returns the same table on every run.

Model prompts carry no warehouse rows. The only data-derived text a prompt may contain is the label values (for example queue names) the user is already allowed to see, and only in the repair message for a failed filter.

### 2. What the Answer Card Contains (the screen is not built)

The answer record holds what a screen needs: the parsed parameters, the exact executed SQL with its parameters, the sources, the certified flag and the notes ("uncertified", "one repair", "period after the as-of date"). The editable parameter chips, the working drawer and the suggested follow-up questions are design intent. No screen has been built, so none of them exists yet.

### 3. Evidence of Quality

| Check | Result |
|---|---|
| Released dev questions through the full pipeline (`--llm`) | 14 pass, 6 text answers marked for human review, 1 fail |
| The one failure (BQ-017, roll rate) | The reference answer's date parser reads month-first dates incorrectly, and our query agrees with the curated data. The team owners are handling it. |
| Reproducibility | 5 full runs of the 35 released questions, 3 of them with the model cache bypassed. All numeric answers are identical. Policy text answers vary in wording on some runs (BQ-001, BQ-002, BQ-019) while the facts are the same. The response cache reproduces the text exactly. |
| Router, first score on question sets it had not seen | Paraphrased metric, policy, SQL and drivers questions: 47.2% with trigger rules only (72-case set, the number we started from). On a 55-case held-out set the rules scored 47.3% and the model fallback 80.0%. On a fresh 39-case set the first score was 84.6%. These sets were later used to diagnose misses, so their current scores (94–96%) are optimistic. |
| Safety layers, first score on unseen wordings | A 24-case refusal set: the rules caught 4 of 16 refusals, the model screen 16 of 16. A final 22-case set, scored once: 12 of 12 refusals and 6 of 6 legitimate look-alike questions passed. One policy question about elderly customers was wrongly refused. |
| Generated SQL | 30 unseen questions, each checked against a reference query written independently. After the fixes, all 30 return correct values under a defensible reading. Five differ from the reference in layout (columns versus rows), units (a percent where the reference used a fraction) or a definition choice (counting cases cured by cure date versus outcome). Before the fixes, one question silently counted all outbound contacts as calls; that class is now blocked. |
| Adversarial probes | Writes (`DELETE`, `UPDATE`, `DROP TABLE`) never ran. Bulk identity pulls were refused or rejected by the validator. A question that filtered on a label that does not exist, such as `reported_bucket = 'default'`, returned a confident 0 before the label check; it now says the value does not exist. |
| Automated tests | 651 |

### 4. Known Limits (stated plainly)

- The refusal and injection screens are rule lists plus a model layer, not a trained classifier. They fail closed on what they do not recognise, and the model layer catches new wordings, but each layer can miss some. The screen can also refuse a legitimate question (one seen).
- The generated-SQL path is tested on 30 questions, mostly single-table and simple joins. It marks every answer uncertified. A valid query can still use the wrong definition (for example "late stage" read as a queue or as the last two delinquency buckets); the card does not yet say which reading was used.
- The router scored 94–96% on its later evaluation sets, but those sets were used to diagnose misses, so the honest range is lower: the first scores on unseen wording were 47–85% before the model fallback was added. A close call returns a clarifying question rather than a guess.
- Customer-call questions depend on P4's function, which is not connected.
- The model-backed stages need the provider key at run time. The embedding matcher and Aho-Corasick index are optional packages (`requirements-router.txt`). Without them the router falls back to its deterministic rules and scores lower.
- Not built: the screens, small-group disclosure limits for tiny breakdowns, and the DeBERTa injection classifier, the compressed Radix Trie and learned schema embeddings that earlier drafts described.

---

## Heading: Scalability, Part 2: Real-Time / Query Serving (merge with P1's part under one "Scalability" heading)

### 1. Measured Latency (development data, one machine)

| Stage | Technology | Measured |
|---|---|---|
| Injection screen and refusal gate (rules, Aho-Corasick) | regular expressions, pyahocorasick | 0.14 ms |
| Router (trigger rules) | regular expressions | 0.25 ms |
| Metric matcher (uncached) | bge-small ONNX on CPU | 10.7 ms median (11.4 ms p95) |
| Join hint for the SQL prompt | networkx over approved join keys | 0.7 ms |
| Validation of one statement | sqlglot | about 160 ms |
| Certified metric, end to end, no model | template + DuckDB | 180–270 ms median (335 ms worst seen) |
| Drivers ("why did it change"), end to end, no model | several certified queries | 1.26 s |
| Model safety screen, per question (uncached) | hosted Nemotron via OpenRouter | 1.3–2.5 s |
| Model router fallback (uncached, only when the rules do not place a question) | hosted Nemotron | 1.1–2.2 s |
| Generated SQL, end to end, uncached | screen + generation + validation + execution | 3.3 s (one question) |

With the model safety screen on, every question makes one language-model call, so an uncached certified metric takes about 2.5 s end to end and a repeated one, served from the response cache, takes well under a second. The `--no-cache` flag on the benchmark runner makes every call fresh. A full uncached run of the 35 released questions took 1 minute 39 seconds. We did not measure concurrent users, and the earlier estimate of an answer in under 2 seconds with a 12 ms classifier and 600–1,200 ms generation was never measured and does not hold with the screen on.

### 2. Serving Architecture

- **Embedded, read-only DuckDB.** Each query opens the database read-only. There is no server to run. The curated gold views are the only tables a question can reach, and the validator adds the as-of limit and the role's scope inside every scan.
- **Heavy history tables are not scanned by questions.** The 12.6 million-row snapshot view is reachable only through the generated-SQL path under the row cap and the 5-second timeout. Certified metrics use their reviewed templates.
- **Response cache.** The language-model wrapper caches every response keyed by the whole request (model, prompts, settings, prompt version), so repeated questions and reruns are free and reproducible.
- **Concurrency is untested.** Read-only snapshots allow many sessions in principle. We have not load-tested it.

---

## README entry: what changed from the design

| Earlier design | What is built |
|---|---|
| DeBERTa-v3-small (ONNX) injection classifier, 12 ms | Rule patterns plus a hosted-model safety screen that can only add refusals. No DeBERTa model was trained or shipped. |
| Aho-Corasick automaton compiled from a Radix Trie, 2 ms | `pyahocorasick` automaton over phrases generated from the sensitivity registry, with boundary-aware regex fallback. No separate Radix Trie. 0.14 ms for the rules together. |
| Embedding matcher (all-MiniLM) for metric selection | FastEmbed/ONNX `bge-small-en-v1.5` on CPU with similarity and margin thresholds, behind trigger rules, with a hosted-model fallback for what remains. |
| Steiner-tree schema pruning to about 15 columns, with validator enforcing the tree | Steiner path used only as text hints in the SQL prompt. The validator enforces approved join keys, not the tree. No pruning of the schema. |
| Router with six categories including `comparison` and `guardrail` | Seven outcomes: `metric`, `drivers`, `rag_corpus`, `rag_customer`, `sql`, `incident`, `cannot_answer`. Comparisons are metrics with two periods or drivers. Guardrails are the refusal gate and screens. |
| LLM extracts operational parameters into the certified formula | Parameters are parsed in code (dates, breakdowns, filters). The model never supplies a parameter of a certified metric. |
| Editable parameter chips, working drawer, follow-up suggestions | Answer record only. No screen built. |
| Latency estimates 0.65–1.3 s to first result | Measured, with the screen on: about 2.5 s uncached for a certified metric, under a second cached. |
| Row cap `LIMIT 100` | Row cap 500, 5-second timeout. |
| Not in the earlier design | Model fallback and model safety screen (additive only), label-value and implied-channel checks for generated SQL, stable row ordering, response cache and `--no-cache` for reproducibility, held-out evaluation sets. |
