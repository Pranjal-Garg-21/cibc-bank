# Team status: Anushka's machine (P1)

**Snapshot: 2026-10-03, evening IST.** Short-lived note so nobody repeats work. Ask Anushka before relying on it; delete it when it stops being useful.

Details: [`curation.md`](curation.md) (results), [`documentation.md`](documentation.md) (what the code does), [`../requirements.md`](../requirements.md) (build on your own machine).

---

## 1. Running now

**Nothing.** Curated, identity and gold builds have finished and no process is holding the database. Anushka's warehouse (`warehouse/trustissues.duckdb`) is complete and verified, but it is **not shared**: it lives only on that machine and is gitignored. Each teammate builds their own.

## 2. Done on Anushka's machine (do not repeat the investigation)

| Done | Result |
|---|---|
| Raw load, all 31 tables | All match the release row counts. 25,000 transcript files loaded |
| Curated build, all 31 tables | `scripts/verify_warehouse.py` passes. **0 exact duplicate rows anywhere.** About 1.8 hours on the reference laptop |
| Standardised identity columns | Validated on the real data: name parse 99.5% (last) / 99.9% (first) against the CRM's own columns; ALL-CAPS name order confirmed (cards first-last, loans last-first) |
| One real data defect found and fixed | `account_monthly_snapshot.month_end_date` was in 4 formats (10% non-ISO). Recovered with certainty, 0 NULLs now |
| Sensitivity labels | Generated; overdraft columns usable (decided); derived columns inherit PII labels |

**P3: `curated.promises_to_pay` exists** and takes seconds to build on your machine: `python -m trustissues.factory.curate.build_curated --only promises_to_pay` (after the raw load).

## 2b. Data quality report (new)

`reports/data_quality_report.md` lists every issue found, how many records it affected and how it was handled (66 issues, 37 clean checks; now also codes against `code_lookups`, identity outcomes and anomaly detection, sections F-H). It is **generated** from `config/dq_checks.yaml` by `python -m trustissues.factory.quality.dq_report`. **The plan assigns `dq_report.py` to P2.** Anushka built it first so the submission item exists; P2 should extend `config/dq_checks.yaml` instead of writing a second one. Anushka has since added the identity checks (I1-I8), code-list checks (V0-V16) and anomaly checks (A1-A3); the anomaly detail is in `reports/anomaly_report.md`.

The run also added columns to the curated tables. **Use these instead of the originals:** `contact_history.channel_resolved` (not `channel`), `deposit_accounts.cif10` (not `src_customer_ref`, which lost its leading zeros in 20% of rows), `customers.salutation_std`. **Flag columns** (`dq_*`, listed in the report) mark rows with problems that cannot be repaired, such as `external.dq_inquiries_inconsistent` and `deposit_accounts.dq_non_cad`. Rows pointing at a CRM customer that does not exist are listed in `curated.dq_orphan_customer_refs`.

## 3. What to know before matching identities (from the real data)

- **Duplicates are business-level**, not exact rows. 5,950 CRM records are flagged `duplicate_of_crm_id`.
- **Card phones are weak evidence:** 58.2% are masked (only a prefix and suffix survive).
- **Dates of birth are no longer ambiguous in any identity table.** 36.3% of card dates and 12.6% of CRM dates could be read two ways in the text; they are now settled (CRM by its own clean `date_of_birth`, cards by a day-first convention inferred from the data). `std_dob_basis` says how each one was decided. See `ambiguous_values.md`. The card rule was an inference; identity matching has now confirmed it (99.6% of linked cards agree with the CRM DOB, see `identity.md` §3).
- **Deposit names are surname plus initials only** (the first name is just an initial in 99.8% of rows).
- 3.9% of bureau (`external`) rows have no name.

## 4. GitHub

Everything built is on `main` except `config/sensitivity.yaml` (generated; run `python -m trustissues.factory.ingest.dictionary` to make yours) and the data itself (gitignored).

## 5. Identity and gold: built (new)

`gold.customer_xref` (every source customer once → 1,011,960 people since the 4 Oct rebuild with the manual verdicts), `gold.identity_review_queue`, `gold.customer_relationship`, `gold.c360_customer`, `gold.c360_account` are built on Anushka's machine. Rebuild yours with `python -m trustissues.factory.pipeline --from text_controls` (about 8 minutes after curated; needs `pip install splink scipy pyarrow`). Results, rules and measured precision: [`identity.md`](identity.md) and `reports/identity_report.md`.

**For everyone joining collections data to customers:** in about 0.3% of collections cases the listed accounts belong to one customer while `coll_customer_ref` names a different, real customer (the IDs differ by one character, almost certainly a typo). Use `gold.case_customer`: it gives the account holder's golden ID, the named customer's, and a `case_customer_mismatch` flag.

**P2:** the review queue (4,154 pairs, Splink and LLM reasons included) is ready for the Identity Review page; decisions go in `data/review/identity_decisions.csv`. **P4/whoever has an API key:** run `python -m trustissues.factory.identity.build --from second_opinion` to get LLM verdicts on the 3,343 grey-zone pairs.

## 5b. Consumer views: published (new)

`gold.promises_enriched`, `customer_safeguards`, `decisioning_customer` (point-in-time history), `customer_timeline`, `bucket_transitions` and `case_customer` are published through the contract gate. **Several defaults need your confirmation: see [`consumer_views.md`](consumer_views.md) §1** (P3: label, history grain, CRM dating; P4: which notes are used and our masking; P2: bucket grain). `semantic_*` (P2's six Ask tables) are built and published too since 4 Oct ([`semantic_views.md`](semantic_views.md)).

## 5c. Not started (Layer 1, owned by P1)

Running `mapping_assistant.py` against the API (written and tested offline; goes through `common/llm.py`, needs `OPENROUTER_API_KEY` in `.env`), checksum verification of the voice recordings (dropped for now), and a single end-to-end timed pipeline run. **Ask Anushka before starting any of them.**

## 6. Before you run anything

- **The curated build is slow and uses about 1 core of 12**, so extra cores do not help. Build only the tables you need with `--only` (`requirements.md` section 4a).
- **One writer per DuckDB file.** Copy a database rather than sharing one, and check the copy with `python scripts/verify_warehouse.py`.
- Avoid putting the repo in a OneDrive-synced folder.
- The dataset is synthetic.
