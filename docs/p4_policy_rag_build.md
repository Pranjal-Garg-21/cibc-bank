# Policy RAG: build notes (P4)

Status of the policy path of Layer 2 (design: `p4_design_sections.md` §3). This file records what is
built, how to run it, what the data taught us, and what is still open. Keep it current as the path grows.

## 1. Status

| Step | File | State |
|---|---|---|
| Chunk documents by section, attach metadata | `src/trustissues/ask/rag/policy_ingest.py` | Done, tested |
| Filter, keyword search, expand, attach hardship rows | `src/trustissues/ask/rag/policy_search.py` | Done, tested |
| Vector search (Chroma + embeddings) | hook `vector_fn` in `policy_search.py` | **Not built**: embedding provider open (D10) |
| Reranker | hook `rerank_fn` in `policy_search.py` | **Not built**: model open (D12) |
| Single LLM client: retries, cache, JSON output, tier models from config | `src/trustissues/common/llm.py` | Done, tested with a scripted stand-in. **Provider not final**, see below |
| Audit log (every call: prompt, sources, answer, model, prompt version) | `src/trustissues/common/audit.py` | Done, tested |
| Answer generation and the I6 response | `src/trustissues/ask/rag/answer.py` | Done, tested. Not yet run against a real model |
| Role to document mapping | parameter `confidentiality` | Deferred (D21) |

The search runs offline on the raw layer only and does not depend on curation or the golden customer table. `answer.py` calls
an LLM through `llm.py` (provider: OpenRouter, model: NVIDIA Nemotron 3 Super, set in `config/settings.yaml`). The unit tests use a
scripted stand-in for the model and check our code around it (retries, cache, audit, quote checking, not-found); the model's own
writing is checked by running real questions with `scripts/ask_policy.py`.

## 2. Run it

```bash
source .venv/bin/activate
python -m trustissues.ask.rag.policy_ingest      # writes warehouse/rag/policy_chunks.jsonl (51 chunks)
pytest tests/test_policy_ingest.py tests/test_policy_search.py
```

Ask a question and see what happened (needs `OPENROUTER_API_KEY` in the environment or in a gitignored `.env`):

```bash
python scripts/ask_policy.py "Does the Do Not Call List stop collection calls?"
python scripts/ask_policy.py "Les appels de recouvrement sont-ils bloqués par la Liste nationale de numéros exclus?"   # answers in French
python scripts/ask_policy.py "What was the reduced payment in 2025?" --as-of 2025-06-01
python scripts/ask_policy.py "..." --show-sources     # print the exact text the model was given
python scripts/ask_policy.py "..." --no-hardship      # skip the hardship table (only needed while another process holds the database)
python scripts/ask_policy.py "..." --lang FR          # force the answer language (normally detected)
```

```python
from datetime import date
from trustissues.ask.rag.policy_search import PolicyIndex

ix = PolicyIndex.load()
res = ix.search("Does the Do Not Call List stop collection calls?", as_of=date(2026, 9, 28), language="EN")
for s in res.sections:
    print(s.citation(), s.score)      # {'source_type': 'policy', 'id': ..., 'version': ..., 'section': ...}
res.hardship_rows                     # programme rows from the table, valid on as_of
```

`search()` filters: `as_of`, `language`, `product`, `province`, `confidentiality`, `doc_types`. Sizes: `n_candidates=20`,
`top_k=5` (the proposed values in D12, tune on the benchmark).

## 3. How it works

```
reference_documents (raw) + files/docs/*.md
        |  policy_ingest: one chunk per section, metadata attached
        v
warehouse/rag/policy_chunks.jsonl
        |  PolicyIndex.search(query, as_of, ...)
        |    1 filter   version in force on as_of, confidentiality, product, province, doc type
        |    2 search   BM25 keyword  (+ vector via vector_fn, merged by reciprocal rank fusion)
        |    3 rerank   optional rerank_fn
        |    4 expand   each hit -> its full parent section, one per (document family, section)
        |    5 attach   hardship_programs rows by SQL, valid on as_of, best match first
        v
SearchResult { sections[], hardship_rows[], as_of_date, candidates_considered }
```

### 3.1 Chunking (`policy_ingest.py`)

The 19 documents use four layouts, and each is handled:

| Layout | Documents | Section ID used |
|---|---|---|
| `## §4.2 Title` headings | policies, procedures | the heading ID (`§4.2`) |
| `**Q1. ...**` blocks | FAQ | `Q1`, `Q2`, ... |
| Programme table | `HPT-2026` | one chunk per row, keyed by programme ID (`HP-LOAN-RED3`); the table header is kept in each chunk |
| No headings | scripts, incidents, release note, data contract, QC annex | `(whole document)` |

- A section over 180 words is split at sentence boundaries with a one-sentence overlap. A split never crosses
  a section boundary. Today no section is that long, so nothing splits; the logic is covered by a test.
- Each chunk carries `section_text`, the whole parent section, which is what retrieval returns (D12).
- The chunk text starts with the document title and section ID so a section's own name is searchable.

### 3.2 Search (`policy_search.py`)

- **Version in force (D4).** Documents are grouped into a family (a superseded document points at its successor
  through `superseded_by`) and a language. For each group we keep the latest version with
  `effective_date <= as_of`. This is deliberately not "`superseded_by` is empty", which is right for today and wrong
  for a question about a past date. Languages are versioned independently.
- **Keyword search.** BM25Plus over the chunk text plus its document ID, so a query that names `POL-COLL-004` or
  `§4.2` finds it by exact reference. The tokenizer keeps references whole. BM25Plus is used because the plain
  Okapi variant scores a term as zero when it appears in half the documents, which drops real matches in small pools.
- **Hybrid (D11).** If `vector_fn` is supplied, keyword and vector rankings are merged by reciprocal rank fusion
  (constant 60), which does not depend on the two scores sharing a scale.
- **Expansion (D12).** Hits become whole sections, de-duplicated. When the same section exists in EN and FR, the
  user's language is kept.
- **Hardship rows (D5).** Rows from `hardship_programs` linked to the retrieved sections
  (`policy_section` for POL-COLL-004, programme ID for HPT-2026), valid on the as-of date
  (`effective_from <= as_of <= effective_to`; `9999-12-31` means current), filtered by product if given, and
  limited to the 3 that best match the question. The caller treats the table as authoritative where text disagrees.

### 3.3 Language: English is the reference (decision)

- **Reference documents are English.** Wherever an English document has a French rendering (`POL-COLL-001-FR`,
  `POL-COLL-004-FR`), only the English one is searched, because the French versions are condensed summaries that
  drop sections (see section 4). A French-only document with no English twin (`REG-SUM-001-QC`, the Quebec annex)
  is still searched. Code: `translation_ids()` in `policy_search.py`.
- **The answer is in the asker's language.** `common/language.py` detects EN or FR from the question with a small
  deterministic word and accent count (no model call, same result every time; unclear text defaults to English).
  Nobody passes a language flag in normal use.
- **French questions:** `answer.py` translates the question to English with the cheap `bulk` model, only to search;
  the search runs on the original wording plus the translation (so the French-only Quebec annex still matches). The
  model then writes the answer in French from the English sections. **Quotes stay in English**, word for word, so they can
  still be checked against the source. If the translation call fails, the original question is searched.
- **Why not French documents too:** a French question would otherwise find the shorter French summary and miss
  facts that only the English document has (the Do Not Call List, the Sunday hours, the 30-day pause).

## 4. What the data showed

| Finding | Consequence |
|---|---|
| The corpus is small: 19 documents, about 2,500 words, 51 chunks. | The "whole corpus in context" option rejected in the design only because the size was unknown is now viable. Plan to compare it against retrieval on the `rag_corpus` questions and report the result. |
| Superseded versions are present on purpose: POL-COLL-004 v4.1 (reduced payment 40% for 2 months) and call script v1.0. | The version filter is required. A test asserts neither appears for the 2026-09-28 as-of date. |
| The FAQ contradicts the policy: it says the reduced-payment programme lasts 6 months; the policy and the table say 3 months at 50% (benchmark BQ-026). | The FAQ can rank first. The answer step must prefer the table and the current policy, and note the conflict (D5). |
| Document IDs differ from file names (`POL-COLL-004-V4.1` is `POL-COLL-004_v4.1_EN.md`). | Always read `file_path` from `reference_documents`; never build a path from the ID. |
| One document is Quebec-only (`REG-SUM-001-QC`, French, `applies_to_provinces = ["QC"]`). | Province filter hides it from non-Quebec questions. |
| **The French documents are condensed summaries of the English ones, not equal translations.** Compared section by section: no number in a French section differs from its English counterpart, but 5 English sections have no French version at all (POL-COLL-001 §5.3 and §5.5, POL-COLL-004 §4.1, §4.3, §4.4) and the French sections are about a third of the length (the Sunday 13:00-17:00 window, the 30-day contact pause and the 90-day re-review are missing). Only 3 of 19 documents have a French version. | English is the reference (section 3.3). Worth reporting to the organisers on the doubts channel. |
| Every document has `confidentiality = internal`. | No role split is possible from the data yet; role mapping stays deferred (D21). |

## 5. Tests

`tests/test_policy_ingest.py` (chunk shapes) and `tests/test_policy_search.py`:

- version in force for current, past and pre-history dates; languages versioned separately
- confidentiality and province filters applied before search
- reference tokens kept whole; exact-reference search by document ID
- section expansion and language preference; vector results fused with keyword results
- **Acceptance on dev benchmark questions** BQ-002, BQ-005, BQ-019 and BQ-026: the gold section is in the top 5
- hardship row for `HP-LOAN-RED3` is the table's current version (50%, 3 months)

## 6. Known limits and next steps

1. **Keyword-only ranking is weak on paraphrase.** For BQ-026 the wrong FAQ entry ranks first and the correct
   programme row (`HP-LOAN-RED3`) is not in the top 5 sections, although the table lookup recovers it. For BQ-002 the
   gold section ranks fourth. Vector search and a reranker are the fix; both slot in through the hooks.
2. **Chroma and embeddings.** Needs a provider choice (multilingual, EN and FR). The index is 51 chunks, so
   embedding cost is negligible.
3. **`answer.py`.** Fixed structure of answer, quoted evidence and citation; `not_found` when the sources do not
   answer (D13); table wins over text (D5). Needs `llm.py` and `audit.py`.
4. **Open items** carried from the design: sub-chunk size, reranker model, how `confidence` is computed, the join of
   `hardship_programs.policy_section` to sections (currently matched on the section ID `§4.2` / `§4.4`, which works for
   this data), and role mapping.
5. **Whole-corpus comparison** (section 4, first row).

## 7. Guarantees enforced in code, not in the prompt (`answer.py`)

| Guarantee | How |
|---|---|
| No sources, no guess (D13) | Empty retrieval returns `not_found` and the model is never called |
| No invented evidence | Every quoted line must appear word for word in the source it names, or it is dropped |
| No unsupported answer | An answer left with no valid quote is returned as `not_found` |
| Citations are real | Built only from the sources whose quotes survived, in the I6 `Citation` shape |
| Table beats text (D5) | Hardship rows are labelled AUTHORITATIVE in the prompt; a disagreement is added to the answer as a note |
| Everything is logged (D20) | Each call, including cache hits, writes a row to `gold.audit_log` |

## 8. Retrieval upgrade: vector search, reranker, whole corpus (2026-10-03)

`ask/rag/retrievers.py` fills the two hooks with local models (no network call, no cost per question):
`intfloat/multilingual-e5-small` embeddings (EN + FR) and the `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`
reranker. With 51 chunks the vectors are an in-memory matrix cached in `warehouse/rag/policy_vectors.json`, not a
Chroma collection (D9 changed: same result, one fewer service). `PolicyIndex.whole_corpus()` skips search and gives the
model every section in force (40 sections, ~4K tokens), after the same version, role and province filters.
Mode is `policy_rag.mode` in `config/settings.yaml`, or `mode=` on `run_policy`.

Comparison on 16 questions with a known correct section (`scripts/compare_policy_modes.py`,
`reports/policy_retrieval_comparison.md`):

| mode | correct section in the 5 the model reads | MRR | search time |
|---|---:|---:|---:|
| bm25 | 13/16 | 0.61 | 2 ms |
| hybrid | 13/16 | 0.74 | 0.2 s |
| hybrid_rerank (default) | 15/16 | 0.82 | 1.7 s (CPU) |
| whole_corpus | 16/16 by construction | - | - |

BQ-002 moves from rank 4 to 1. BQ-026 stays at rank 2 (the FAQ's contradicting entry is still first; the answer step
prefers the hardship table and notes the conflict). Paraphrases that keyword search missed entirely (proof of
earnings, a bereaved customer) are now found. Still to do once the API key has credit: `--answers`, which compares
answer quality between hybrid_rerank and whole_corpus, then pick the default.

**Answers compared (free Nemotron 3 Super, `--answers`):** whole_corpus cited the correct section on 16/16 questions,
hybrid_rerank on 13/16 (it answered P-03, a complaint question, with hardship steps, and cited a weaker source for BQ-026
and P-05). Answer time was the same (2-3 s). **Default changed to `whole_corpus`.** The corpus is ~4K tokens, so the
model reading all of it is cheaper than a wrong answer; the version, role and province filters still run first. Search
(hybrid_rerank) stays as the option for when the corpus grows past what fits comfortably in a prompt.
