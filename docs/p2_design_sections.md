# P2 Design Sections: For Final System Design PDF

## Heading: Layer 2: Insight and NLP Platform (Structured)

### 1. Architectural Overview & Query Pipeline
Layer 2 provides natural-language conversational analytics ("Collections Ask") over the unified Collections 360 data product. Operating over 31 tables and 1,422 columns across about 1M customers, the system addresses the classic Text-to-SQL problems of context bloat, join hallucinations and double-counting with a staged pipeline that checks every question before and after the LLM:

0. **Access check (before anything else):** the user's role sets what they can see (agents: assigned cases only; team leads: portfolio aggregates; analytics: non-PII data). The role is passed into every later stage and into SQL validation.

1. **Stage 0: Adversarial Prompt-Injection Defense:** A lightweight, quantized classification model (DeBERTa-v3-small running via ONNX Runtime in ~12ms) inspects the raw prompt for adversarial jailbreaks, roleplay bypasses, system-prompt extraction attempts, and obfuscated SQL payloads. Malicious prompts are rejected before reaching downstream parsers.
2. **Stage 1: Deterministic Compliance & Refusal Gate:** Queries passing Stage 0 enter a lexical index implemented as an Aho-Corasick automaton compiled from a Radix Trie. Refusal triggers are generated from the same protected-attribute registry as Layer 1 (the 12 columns tagged `Protected attribute` in the data dictionary, plus their synonyms), from the columns tagged `Direct PII`, and from out-of-scope topics. Matching is whole-word, so "average" or "ageing report" does not trigger a refusal. Questions that ask to filter, group or compare by `age` or `age_band` are refused (age is kept out of drivers and models by policy), while "age of account" and "ageing buckets" are allowed. A hit short-circuits the pipeline in about 2 ms and returns a refusal reason with a safe alternative question.
3. **Stage 2: Deterministic As-Of Date Resolution:** To prevent the fatal time-displacement bug common in frozen historical datasets (the hackathon dataset snapshot is anchored at 28 September 2026), all relative temporal expressions ("this week", "last 90 days", "this month") are deterministically resolved against the fixed benchmark anchor (`AS_OF_DATE = '2026-09-28'`). The resolved date boundaries are passed as explicit date literals into query parameters and displayed as editable chips on the UI answer card.
4. **Stage 3: Query Routing & Metric-First Intent:** Queries are classified into six benchmark categories: `metric`, `drivers`, `comparison`, `rag_corpus`, `rag_customer`, and `guardrail`.
   * **Metric & Comparison Queries:** The system maps the question to certified metrics in `metric_definitions` using precomputed semantic embeddings of metric names, business definitions, and synonyms. The LLM extracts only operational parameters (filters, dates, segments), binding them into the certified SQL formula with `default_filters` automatically enforced.
   * **Drivers Queries:** Handled via a structured Drivers Decomposition Pipeline: the system evaluates the certified metric and splits it across an approved whitelist of operational dimensions (`product`, `stated_delay_reason`, payroll-delay band, `prior_broken_ptp_count`). Protected attributes are never allowed as split dimensions. It ranks each factor's contribution to the change.
   * **Policy & Customer Transcripts:** Handled by routing the request to Layer 2 RAG (P4) via Interface I6.
5. **Stage 4: Grain-Aware Schema Pruning (Steiner Join Tree):** For ad-hoc queries where no certified metric exists (the answer is labelled "uncertified"), the Radix Trie and embedding matcher extract terminal tables and columns. To reduce "fan traps" and "chasm traps" (joining multi-account tables across a customer duplicates rows and double-counts balances), the join graph follows the Candidate Network idea of DISCOVER (VLDB 2002). Foreign-key edges carry multiplicity annotations; many-to-many paths are heavily penalised and sent through certified pre-aggregated views (`gold.c360_customer`) or isolated CTE subqueries. A Steiner tree (via `networkx`) extracts the minimal join path and prunes the schema from 1,422 columns to roughly 15 relevant ones. The graph is built over the gold layer, including the `bureau_history` link through `bank_subject_ref` in the crosswalk. Pruning quality is measured as schema-linking recall against each benchmark question's `expected_tables`.
6. **Stage 5: AST Validation & Read-Only Execution:** Before execution, the generated SQL is parsed into an Abstract Syntax Tree (AST) using `sqlglot`. The validator enforces: (a) read-only `SELECT` only, (b) all tables and columns come from the pruned schema, (c) joins follow the Steiner tree edges, (d) the metric's `default_filters` are present, (e) no direct-PII or protected columns are selected and the user's role scope is applied, and (f) a row cap (`LIMIT 100`). On failure the query is re-prompted once with the error; if it still fails, the system says the question cannot be answered reliably. Validated queries run on in-memory DuckDB marts, typically in tens of milliseconds.

### 2. UI Answer Card Specification
Every query returns an auditable Answer Card containing:
* **"Show What It Understood":** Editable parameter chips (e.g. `[Product: Personal Loan]`, `[Window: 21 Sep - 28 Sep 2026]`, `[Status: Broken PTP]`) that analysts can modify with a single click.
* **"Show the Working":** A collapsible provenance drawer displaying the exact generated SQL, source tables, and execution latency.
* **Executive Summary & Next Actions:** A 2-sentence natural language synthesis of findings accompanied by 3 clickable follow-up questions.

---

## Heading: Scalability, Part 2: Real-Time / Query Serving (merge with P1's part under one "Scalability" heading)

### 1. Interactive Latency Budget (planning estimates, to be measured)
To serve supervisors, strategy leads and operations teams during peak hours, the query pipeline targets an answer in under 2 seconds. Everything except the LLM call takes tens of milliseconds, so the hosted LLM call dominates the budget:

| Processing Stage | Tool / Technology | Algorithmic Complexity | Target Latency |
|---|---|---|---|
| Prompt Injection Pre-Filter | DeBERTa-v3-small (ONNX) | Fixed transformer pass | 12–15 ms |
| Lexical Scan & Refusal Gate | pyahocorasick (Radix Trie) | O(\|Query\|) linear scan | 1–2 ms |
| Date Resolver & Router | Regex + Vector Sim (all-MiniLM) | Cosine similarity over metrics | 8–12 ms |
| Steiner Tree Schema Pruning | networkx (31-node graph) | Minimal Steiner tree approx. | 3–5 ms |
| Parameter / SQL Generation | Hosted LLM API (Pruned Context) | ~250 input tokens | 600–1,200 ms (estimate) |
| AST Syntax Validation | sqlglot AST Parser | Single-pass grammar traversal | 2–3 ms |
| Analytical Query Execution | DuckDB In-Memory OLAP | Columnar vectorized scan | 15–30 ms |
| **Total to first result** | | | **~0.65–1.3 s** |

The two-sentence summary and follow-up suggestions are generated after the result is shown (streamed), so they do not delay the table.

### 2. High-Throughput In-Memory Architecture
* **Columnar Vectorized Execution:** Curated gold-layer tables (`gold.c360_customer`, `gold.c360_account`, `gold.customer_xref`) and historical transaction marts are persisted as compressed Parquet files and mapped into an in-memory DuckDB analytical engine. DuckDB’s vectorized execution engine performs multi-threaded scans and aggregations over 1M+ rows in milliseconds with no distributed cluster.
* **Zero-Copy Schema Indexing:** The Radix Trie and Aho-Corasick automaton are built once at catalog sync time, residing in memory (<10MB RAM footprint). Query parsing scales strictly with the character length of the user question, independent of the size of the underlying database.
* **Multi-User Read-Only Concurrency:** The query engine operates over read-only snapshots, so many analyst sessions can run side by side (concurrency to be load-tested). Heavy joins on large history tables (e.g. the 10M-row `transactions` table) go through pre-aggregated views (`gold.bucket_transitions`, a salary summary) so they do not slow frontline queries.
