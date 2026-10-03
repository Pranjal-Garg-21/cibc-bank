# P4 Design Sections: For Final System Design PDF

**Owner:** P4 (LLM and Product Lead)
**Sections owned:** Architecture diagram (page 1), Summary, AI usage, Governance
**Contributed to:** Layer 2 (RAG paths, handed to P2), Layer 3 (LLM text features, handed to P3)

**Status labels used in this file**
- **Decided**: agreed by P4.
- **Proposed**: suggested resolution, awaiting P4 confirmation.
- **Open**: still to be decided.
- **TBC**: cannot be confirmed until the dataset is released (3 Oct).

---

## 0. Decision Log

| # | Area | Decision | Status |
|---|---|---|---|
| D1 | Customer path method | Long context: the customer's full redacted timeline is given to the LLM. No vector search. | Decided |
| D2 | Number of vector indexes | One index, for policy documents only. Customer text is never embedded or stored in a shared search index. | Decided |
| D3 | Policy document types | All nine `reference_documents` types are indexed, filtered by role before search. | Decided |
| D4 | Policy versions | All versions are kept. Retrieval defaults to the version in force on the as-of date. | Decided |
| D5 | Hardship programme rules | Read from `hardship_programs` by SQL only, not indexed as text. Where text and table disagree, the table wins. | Decided |
| D6 | Chunk unit | One chunk per policy section; long sections are split further, and a chunk never spans two sections. | Decided (sub-chunk size Open) |
| D7 | Chunk overlap | Small overlap of 1 to 2 sentences between sub-chunks of the same section. | Decided |
| D8 | Language | Search both EN and FR documents; answer in the user's language. | Decided |
| D9 | Vector store | Chroma | Decided |
| D10 | Embedding model | Hosted API multilingual embedding model (EN + FR). | Decided (provider Open) |
| D11 | Search type | Hybrid: vector search plus keyword search. | Decided |
| D12 | Retrieval | Retrieve a wider candidate set, rerank to the best few, then expand each hit to its full parent section. | Decided (rerank); Proposed (section expansion, k values) |
| D13 | Answer not found | Say "not found in policy" (or "not found in this customer's history") and suggest who to ask. Never answer from general knowledge. | Decided |
| D14 | Answer format | Fixed structure: answer, quoted evidence, citation. | Decided |
| D15 | Customer history cap | Token budget filled newest-first; the answer states the date range covered. | Proposed |
| D16 | Redaction rule | Notes are used only if `redaction_status = auto_redacted`. `needs_review` is excluded, and `none` is excluded until its meaning is confirmed. | Decided (meaning of `none` TBC) |
| D17 | Transcripts | Full turns (agent and customer) included. | Decided |
| D18 | Output to router (I6) | `{answer, citations[]}` plus `confidence` and `not_found`. | Decided (confidence method Open) |
| D19 | Evaluation | Use the `rag_corpus` and `rag_customer` questions in `benchmark_questions` when the dataset is released. | Decided |
| D20 | Logging | Log every question, retrieved item, full prompt and answer. | Decided |
| D21 | Role mapping | Which roles see which document types and confidentiality levels. | Deferred (roles TBC) |

---

## Heading: Layer 2: Insight and NLP (RAG paths), handed to P2

### Condensed text for the PDF (paste into P2's Layer 2 section)

> Questions classified as `rag_customer` or `rag_corpus` go to two separate text paths. **Customer questions** use long context rather than vector search: the customer's full redacted timeline of notes and transcripts is fetched by `golden_customer_id` through the identity crosswalk (so duplicate CRM records are included) and given to the LLM in time order. One customer's history is small while the corpus is large (~1.5M notes), so exact lookup cannot miss a relevant note and scales with the customer, not the corpus; very long histories are capped newest-first and the answer states the period covered. **Policy questions** use hybrid (vector plus keyword) retrieval over all `reference_documents`, chunked by section, filtered by role and by the version in force on the as-of date (old versions are deliberately kept in the dataset), then reranked. Hardship eligibility is read from `hardship_programs` by SQL, never from text. Every answer follows a fixed structure (answer, quoted evidence, citation by document ID, version and section, or note/transcript ID and date), and if the sources do not contain the answer the system says so instead of guessing.

### 1. Router handoff (Interface I6)

**Input from P2's router:**
`{question, question_type, golden_customer_id?, user_role, as_of_date}`

**Output back to the router:**

```
{
  answer,
  evidence[],        // quoted lines from the sources
  citations[],       // {source_type, id, version | date, section}
  confidence,        // method Open
  not_found,         // true if the sources do not contain the answer
  coverage           // customer path only: date range of history used (Proposed, D15)
}
```

### 2. Customer path (long context)

| Step | What happens |
|---|---|
| 1. Access check | The user must have an assigned case for this `golden_customer_id` (agents), or a role allowed to view customer text. Otherwise refuse with reason. |
| 2. Fetch | Read from P1's `gold.customer_timeline` by `golden_customer_id`. The crosswalk (`gold.customer_xref`) means notes filed under any of the person's CRM records are included. |
| 3. Filter | Point-in-time: only items dated on or before `as_of_date`. Notes: `redaction_status = auto_redacted` only (D16). Notes: `retention_until` not passed. Transcripts: `pii_redaction_applied = true` and `pci_redaction_applied = true`. Transcript retention: no retention column in `call_transcripts`; use `contact_history.recording_retention_until` via `contact_id` (TBC). |
| 4. Order and cap | Chronological order. Fill a token budget newest-first (Proposed starting value: 20K tokens, D15). Full transcript turns, speaker-labelled (D17). |
| 5. Generate | LLM answers only from the supplied items, in the fixed answer structure (D14). If not present: `not_found = true` (D13). |
| 6. Return | Output per I6, with `coverage` stating the date range used. |

**Why long context and not vector search (D1):**
- **Reliability:** an exact lookup by customer returns every note; similarity search can miss a note worded differently from the question (e.g. "shifts cut" vs "reduced hours").
- **Scalability:** cost per question depends on one customer's history, not the 1.5M-note corpus. Example note in the dictionary is 19 words (~30 tokens); example transcript is 6 minutes, 58 turns.
- **Freshness:** a new note is answerable as soon as it lands in the table; nothing needs to be embedded first.
- **Build effort:** no need to embed 1.5M notes during the 36-hour build.
- **Safety:** customer text never sits in a shared search index, so it cannot leak into policy answers (D2).

**Known limits:**
- Depends on P1's identity matching; a missed CRM duplicate means missing notes.
- Very long histories are truncated (D15); the coverage line makes this visible.

### 3. Policy path (classic RAG)

**3.1 Source preparation**

| Field role | Fields |
|---|---|
| Text | Markdown file at `file_path` |
| Filtering | `doc_type`, `language`, `confidentiality`, `applies_to_products`, `applies_to_provinces`, `effective_date`, `superseded_by` |
| Citation | `doc_id`, `title`, `version`, `section_ids` |

- All nine document types are indexed (D3): collections rules (`policy`, `procedure`, `call_script`, `hardship_programme_terms`, `regulatory_summary`, `faq`) and operational documents (`release_note`, `incident_report`, `data_contract`). Operational documents let team leads and analysts ask why a metric or feed changed (e.g. the `channel_v2` rename in `contact_history`).
- All versions are kept (D4).
- Number of documents: TBC.

**3.2 Chunking**
- One chunk per section, using the Markdown headings and `section_ids` (D6).
- Long sections are split into sub-chunks; a chunk never crosses a section boundary, so two sections never mix.
- Sub-chunks of the same section overlap by 1 to 2 sentences (D7).
- Each sub-chunk records its parent section, so it can be expanded at retrieval (D12).
- Sub-chunk size: Open.

**3.3 Indexing**
- Chroma vector store (D9), one collection for policy only (D2).
- Hosted multilingual embedding model (D10); EN and FR are both indexed (D8).
- Keyword index alongside it for hybrid search (D11), so exact terms such as `§4.2` or `POL-COLL-004` are found reliably. Keyword method and how the two result lists are merged: Open.
- Metadata from 3.1 is stored with every chunk.

**3.4 Retrieval (in this order)**
1. **Filter before search:**
   - role and `confidentiality` (D21, mapping TBC);
   - version in force on `as_of_date`, meaning the latest version of each document with `effective_date` on or before the as-of date (D4). Filtering only on empty `superseded_by` would be correct for today but wrong for past as-of dates;
   - product and province, where the question or customer gives them (`applies_to_*` contains the value or `ALL`).
2. **Hybrid search:** vector and keyword search, both languages (D8, D11).
3. **Rerank:** retrieve a wider candidate set and rerank to the best few (D12). Proposed: 20 candidates reranked to top 5. Reranker model: Open.
4. **Section expansion (Proposed):** each reranked hit is replaced by its full parent section, de-duplicated. This means top-k counts *sections*, not fragments, so a long section split into many sub-chunks is still read whole. If the same section is found in EN and FR, keep the user's language.
5. **Hardship programmes:** if the retrieved section is linked from `hardship_programs.policy_section`, attach the programme rows by SQL for the version valid on the as-of date (`effective_from` ≤ as-of date < `effective_to`; `9999-12-31` = current) (D5). The column is labelled as an FK to `reference_documents.doc_id` but the example value is a section (`§4.2`); exact join TBC.

**3.5 Answer generation**
- The LLM answers only from the retrieved sections and SQL rows.
- Fixed structure (D14):
  1. **Answer:** plain English, in the user's language.
  2. **Evidence:** the quoted policy line(s).
  3. **Citation:** `doc_id`, version, section, effective date.
- Where text and `hardship_programs` disagree, the table is shown and the conflict is noted (D5).
- If the sources do not answer the question: "Not found in policy", with a suggestion of who to ask (e.g. the document `owner`) (D13).

### 4. Evaluation (D19)
- Run the `rag_corpus` and `rag_customer` questions from `benchmark_questions` once released, scored against its `grading_rubric` within P2's harness.
- Check specifically: correct version cited for old-version questions; refusals for customers outside the user's access; `not_found` returned when appropriate.

---

## Heading: AI usage (P4 section)

### Condensed text for the PDF

> AI is used only where rules cannot do the job, and every AI output is either checked by a deterministic step or reviewed by a human. LLMs read text: they suggest data mappings (Layer 1), judge uncertain identity matches, turn questions into metric parameters, answer policy and customer questions with citations, and extract hardship and delay-reason signals from notes and transcripts. A classical model (LightGBM with SHAP) predicts promise breaks, because numeric risk needs to be reliable, calibrated and explainable. For customer questions we chose long-context reading over vector search because one customer's history is small and exact lookup cannot miss a note; we rejected fine-tuning for policy because a fine-tuned model cannot cite the version it used or update when policy changes. Safety-critical steps (refusals, date resolution, SQL validation, hard stops, eligibility checks, version filtering) use no AI at all.

### Where AI is used across the pipeline

| Layer | Step | AI type | Why AI | Check on it |
|---|---|---|---|---|
| 1 | Source-to-target column mapping suggestions | LLM | Speeds up mapping 1,422 columns | P1 approves |
| 1 | SQL transformation drafts | LLM | Speeds up curation | P1 reviews |
| 1 | Probabilistic identity matching | Statistical model (Splink, Fellegi-Sunter) | Fuzzy matches across name, DOB, phone, postal | Thresholds tuned on a hand-checked sample |
| 1 | Stage 2 matcher for uncertain matches | LLM on serialized records | Handles initials, nicknames, DOB swaps, employer abbreviations | Cannot link alone; disagreements go to human review queue |
| 1 | Load anomaly detection | Statistical | Flags row-count and null-rate shifts | Cross-checked with `ops_events` |
| 2 | Prompt-injection pre-filter (P2) | Small classifier (DeBERTa-v3-small) | Detects jailbreak and injection attempts | Followed by deterministic refusal gate |
| 2 | Metric matching (P2) | Embeddings | Matches paraphrased questions to certified metrics | Certified formula used; LLM fills parameters only |
| 2 | Parameter extraction / uncertified SQL fallback (P2) | LLM | Turns words into filters and dates | `sqlglot` validation against pruned schema; fallback labelled "uncertified" |
| 2 | Policy RAG: embeddings + rerank | Embeddings, reranker | Finds relevant sections in EN and FR | Version and role filters applied before search |
| 2 | Policy and customer answers | LLM | Writes plain-English answers from sources | Must quote evidence and cite; `not_found` instead of guessing |
| 3 | Text features from notes and transcripts | LLM | Hardship signal, delay reason, intent strength, dispute mention | Hand-labelled sample; agent-coded `hardship_mentioned_flag` as weak label |
| 3 | Promise-break prediction | LightGBM | Reliable, calibrated, testable numeric risk | Compared with `model_scores` baseline; fairness check |
| 3 | Explanations | SHAP + template; LLM polish | Plain-English reasons | LLM may reword but cannot change the reasons |
| 3 | Agent-screen customer summary | LLM | Condenses C360 and timeline | Agent sees sources and approves or overrides |

### Where AI is deliberately not used
Refusal gate (trie), as-of date resolution, SQL validation, access checks, policy version filtering, hardship eligibility (SQL), hard stops (cease, deceased, bankruptcy, dispute), channel consent, and hardship/vulnerability routing to humans.

### Alternatives considered and rejected
| Alternative | Rejected for | Reason |
|---|---|---|
| Vector RAG over all notes | Customer path | Can miss notes; must embed 1.5M notes; new notes not searchable until embedded |
| Fine-tuning on policy | Policy path | Cannot cite versions; retraining on every policy change; cannot prove old rules are forgotten |
| Cache-augmented generation (whole corpus in context) | Policy path | Corpus size TBC; version filtering still needed |

---

## Heading: Governance (P4 section)

### Condensed text for the PDF

> **Access:** role-based; agents see only customers on their assigned cases, team leads see aggregates, and policy documents are filtered by role and confidentiality before retrieval (role mapping TBC). **Protected attributes:** the 12 tagged attributes are physically excluded from the decisioning view and are refusal triggers in Layer 2; they are used only to test fairness, never as inputs. **Hardship and vulnerability:** detected from structured flags and LLM text features, and always routed to a certified agent with human approval. **Hard stops** (cease contact, deceased, bankruptcy, dispute, no consent) run before any recommendation. **Text data:** only redacted text reaches an LLM (notes with `redaction_status = auto_redacted`; `needs_review` and, until confirmed, `none` are excluded; transcripts with PII and PCI redaction applied), and retention dates are respected. **Human review:** every customer-affecting action is shown to an agent first, and overrides are logged with a reason code. **Audit:** every question, retrieved source, full prompt, answer and recommendation is logged with model and prompt version.

### Details

| Area | Control | Source |
|---|---|---|
| Access control | Agents: assigned cases only. Team leads: portfolio aggregates. Analytics: full non-PII. PII masked unless role allows. | Team plan |
| Policy documents | Filtered by role and `confidentiality` (`internal` / `restricted`) before search; operational documents only for roles that need them | D3, D21 (mapping TBC) |
| Protected attributes | Excluded from `gold.decisioning_customer`; not indexed for schema linking; indexed as refusal triggers; proxy check on FSA, geo coordinates, accent region | Team plan, P2 |
| Hardship and vulnerability | Always human review; `vulnerable_customer_certified` agent; eligibility checked by SQL in `hardship_programs` | Team plan, D5 |
| Hard stops | Cease, deceased, bankruptcy, open dispute, channel consent applied before any recommendation | Team plan |
| Text into LLMs | Redacted only (D16); point-in-time (dated on or before as-of date) | D16 |
| Retention | Notes: `retention_until`. Transcripts: `contact_history.recording_retention_until` (TBC) | D16 |
| Answer integrity | Fixed answer structure with quoted evidence and citations; `not_found` instead of guessing; current policy version by default | D13, D14, D4 |
| Audit logging | Full prompts, retrieved items, answers, versions logged (D20). Proposed: because logs then contain customer text, the logs themselves are access-restricted and follow the same retention rules. | D20 |
| Hosted models | Synthetic data is acceptable for the hackathon; in production, hosted LLM and embedding calls must run in a bank-approved environment | Team plan |
| Human review points | Identity grey zone, hardship/vulnerability, high-value accounts, uncertain model scores, every customer-affecting action | Team plan |
| Monitoring | Data contract checks and drift; model AUC, calibration, PSI, override rate; LLM feature accuracy on a labelled sample each release | Team plan |

---

## Heading: Summary (P4 section, draft from team plan, not yet reviewed)

> **Problem:** a collections customer's story is split across nine systems with different customer keys, so agents work without the full picture and treatments are not tailored. **Use case:** Next Best Action, recommending the right treatment, channel and timing for each customer with a plain-English reason, and routing hardship or vulnerable cases to a trained human. **Users:** collections agents (act on recommendations), team leads (ask questions, monitor), and collections analytics (own metrics and models).

---

## Architecture diagram (page 1): changes needed

- Replace the single "Policy + customer RAG" box with two paths out of the router:
  - **Customer path:** router → access check → `gold.customer_timeline` (SQL by golden ID) → LLM (long context) → answer card.
  - **Policy path:** router → role + version filter → Chroma hybrid search → rerank → section expansion (+ `hardship_programs` SQL) → LLM → answer card.
- `agent_notes` / `call_transcripts` should flow through Layer 1 (redaction filter) before LLM feature extraction, not directly from source as in the current draft; otherwise the diagram contradicts Governance.
- Mark AI steps and human review points (see AI usage table).
- Include team and member names.

---

## Other P4 responsibilities (from team plan, not yet designed together)

| Item | Current plan | Open points |
|---|---|---|
| LLM text features (I3 → P3) | `{golden_customer_id, source_id, source_ts, hardship_signal, stated_delay_reason, ptp_intent_strength, dispute_mentioned, prompt_version}` | Add evidence quote and confidence? Same redaction rule (D16) applies. Note P2 uses `stated_delay_reason` as a drivers dimension. |
| Agent screen | C360 view, recommendation, reasons, approve/override | Not yet designed |
| Override log (I7 → P3) | `{recommendation_id, agent_id, decision, reason_code, ts}` | Reason code list Open |
| Ask screen front end | Front end for P2's answer card | Align with P2's answer card spec |
| Demo storyline | 5-minute flow in team plan | Lead if selected |
| PDF merge and submission | Merge, consistency check, submit by 10:45 PM IST | — |

---

## Consistency notes for the merge (against P2's draft)

- P2's sub-350 ms latency budget covers the SQL path only. RAG answers generate a full written answer with evidence and will take longer; the Scalability section should say so (latency to be measured).
- P2 routes `rag_corpus` and `rag_customer` to P4 via I6: consistent. I6 output now also includes `evidence`, `confidence`, `not_found` and `coverage`; P2's answer card should display them.
- P2 cites "Grain Theory (arXiv 2026)"; verify the reference before submission.

---

## Open items

| Item | Status |
|---|---|
| Sub-chunk size for long sections | Open |
| Embedding provider; reranker model | Open |
| Keyword method and how vector + keyword results are merged | Open |
| How `confidence` is computed | Open |
| Section expansion and k values (20 → 5) | Proposed |
| Customer history token budget (20K, newest-first) | Proposed |
| Meaning of `redaction_status = none`; share of notes it covers | TBC |
| Transcript retention source | TBC |
| `hardship_programs.policy_section` join to sections | TBC |
| Number of `reference_documents` | TBC |
| Role mapping for documents | Deferred |
