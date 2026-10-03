# Team status: Anushka's machine (P1)

**Snapshot: 2026-10-03 about 13:50 IST.** Short-lived note so nobody repeats work. Ask Anushka before relying on it; delete it when it stops being useful.

Details: [`curation.md`](curation.md) (results), [`documentation.md`](documentation.md) (what the code does), [`../requirements.md`](../requirements.md) (build on your own machine).

---

## 1. Running now

**Nothing.** The curated build finished and no process is holding the database. Anushka's warehouse (`warehouse/trustissues.duckdb`) is complete and verified, but it is **not shared**: it lives only on that machine and is gitignored. Each teammate builds their own.

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

`reports/data_quality_report.md` lists every issue found, how many records it affected and how it was handled (53 issues, 20 clean checks). It is **generated** from `config/dq_checks.yaml` by `python -m trustissues.factory.quality.dq_report`. **The plan assigns `dq_report.py` to P2.** Anushka built it first so the submission item exists; P2 should extend `config/dq_checks.yaml` (for example with the unmatched records and open review items the identity step will produce) instead of writing a second one.

The run also added columns to the curated tables. **Use these instead of the originals:** `contact_history.channel_resolved` (not `channel`), `deposit_accounts.cif10` (not `src_customer_ref`, which lost its leading zeros in 20% of rows), `customers.salutation_std`. **Flag columns** (`dq_*`, listed in the report) mark rows with problems that cannot be repaired, such as `external.dq_inquiries_inconsistent` and `deposit_accounts.dq_non_cad`. Rows pointing at a CRM customer that does not exist are listed in `curated.dq_orphan_customer_refs`.

## 3. What to know before matching identities (from the real data)

- **Duplicates are business-level**, not exact rows. 5,950 CRM records are flagged `duplicate_of_crm_id`.
- **Card phones are weak evidence:** 58.2% are masked (only a prefix and suffix survive).
- **36.3% of card dates of birth are ambiguous** (day and month could be swapped); both readings are kept in `std_dob` and `std_dob_alt`.
- **Deposit names are surname plus initials only** (the first name is just an initial in 99.8% of rows).
- 3.9% of bureau (`external`) rows have no name.

## 4. GitHub

Everything built is on `main` except `config/sensitivity.yaml` (generated; run `python -m trustissues.factory.ingest.dictionary` to make yours) and the data itself (gitignored).

## 5. Not started (Layer 1, owned by P1)

Identity matching (nodes, exact rules, Splink, LLM second opinion, review queue, clusters), gold layer, data-quality checks, `contracts/c360_customer.yaml`, `text_controls.py`, `mapping_assistant.py`, enum validation against `code_lookups`, and the curated build speed-up. **Ask Anushka before starting any of them.** Splink is not installed anywhere yet.

## 6. Before you run anything

- **The curated build is slow and uses about 1 core of 12**, so extra cores do not help. Build only the tables you need with `--only` (`requirements.md` section 4a).
- **One writer per DuckDB file.** Copy a database rather than sharing one, and check the copy with `python scripts/verify_warehouse.py`.
- Avoid putting the repo in a OneDrive-synced folder.
- The dataset is synthetic.
