# Curation: raw to curated

**Status: complete on the real data (2026-10-03).** All 31 tables are built, `scripts/verify_warehouse.py` passes every check, and the 35 unit tests pass. This file is the record of what the curated layer does, what the real data showed, and what is still open. Setup and run commands: [`../requirements.md`](../requirements.md). How values that cannot be read with certainty are handled: [`ambiguous_values.md`](ambiguous_values.md). Component reference: [`documentation.md`](documentation.md).

**The data quality report** (`reports/data_quality_report.md`) is the full account of the issues in the data, how many records each affected and how each was handled. This file covers how curation works; that one covers what was wrong with the data. Curation findings that appear in both agree.

## 1. What curated is

`raw.*` holds every source table exactly as received (all text). `curated.*` is the same data made usable:

| Step | What happens |
|---|---|
| Types | Column types come from the release's `schema.json` (integers, decimals, dates, timestamps, booleans). Blank text becomes NULL. Text, enum, JSON and array columns stay text |
| Exact duplicates | Removed on `_row_hash` (an md5 of the whole source row) |
| Failed casts | The value becomes NULL, the failure is counted in `curated._cast_failures`, and the original text is kept in `<col>__raw` on that row |
| Non-ISO dates | Recovered when exactly one reading is valid, counted in `curated._format_recoveries`, otherwise left NULL with the text kept |
| Identity fields | The five identity sources get `std_*` columns: name, date of birth, phones, postal code, employer (see section 5) |
| Current rows | `<table>_current` holds one row per key for `customers`, `card_accounts`, `loan_accounts`, `deposit_accounts` |

It works for **any** table in `raw` with no per-table SQL. A table that is not in `schema.json` is passed through as text.

```bash
python -m trustissues.factory.curate.build_curated                         # everything
python -m trustissues.factory.curate.build_curated --only promises_to_pay  # just what you need
python scripts/verify_warehouse.py                                         # confirm
```

## 2. Results on the real data

All 31 tables. Build times are from the reference laptop (12 logical cores, 15.7 GB RAM); see section 7 for why they vary so much.

| Table | Raw rows | Curated rows | Exact duplicates removed | Build time |
|---|---:|---:|---:|---:|
| `loan_instalments` | 14,832,472 | 14,832,472 | 0 | 61 s |
| `account_monthly_snapshot` | 12,569,692 | 12,569,692 | 0 | 37 s (recovered 1,255,152 dates in `month_end_date`) |
| `transactions` | 12,401,924 | 12,401,924 | 0 | 108 s |
| `card_statements` | 7,531,283 | 7,531,283 | 0 | 119 s |
| `salary_credit_history` | 6,000,000 | 6,000,000 | 0 | 23 s |
| `bureau_history` | 4,679,562 | 4,679,562 | 0 | 13 s |
| `contact_history` | 2,688,337 | 2,688,337 | 0 | 852 s |
| `model_scores` | 1,718,841 | 1,718,841 | 0 | 14 s |
| `customers` | 1,020,000 | 1,020,000 | 0 | 2,469 s |
| `external` | 1,000,000 | 1,000,000 | 0 | 179 s |
| `deposit_accounts` | 780,000 | 780,000 | 0 | 1,947 s |
| `agent_notes` | 760,594 | 760,594 | 0 | 11 s |
| `card_accounts` | 660,000 | 660,000 | 0 | 521 s |
| `case_assignment_history` | 423,830 | 423,830 | 0 | 8 s |
| `loan_accounts` | 404,500 | 404,500 | 0 | 82 s |
| `collections_cases` | 367,229 | 367,229 | 0 | 110 s |
| `promises_to_pay` | 238,815 | 238,815 | 0 | 2 s |
| `offers` | 40,767 | 40,767 | 0 | 0 s |
| `call_transcripts` | 25,000 | 25,000 | 0 | 1 s |
| `agent_shifts` | 15,317 | 15,317 | 0 | 0 s |
| `voice_samples` | 2,000 | 2,000 | 0 | 0 s |
| `agents` | 1,000 | 1,000 | 0 | 0 s |
| `geo_reference` | 656 | 656 | 0 | 0 s |
| `channel_capacity` | 287 | 287 | 0 | 0 s |
| `code_lookups` | 113 | 113 | 0 | 0 s |
| `benchmark_questions` | 35 | 35 | 0 | 0 s |
| `reference_documents` | 19 | 19 | 0 | 0 s |
| `hardship_programs` | 18 | 18 | 0 | 0 s |
| `metric_definitions` | 15 | 15 | 0 | 0 s |
| `qa_checklist` | 10 | 10 | 0 | 0 s |
| `ops_events` | 6 | 6 | 0 | 0 s |

**Total build time of the latest per-table builds: 1.8 hours.** Every table reconciles: curated rows equal raw rows minus the duplicates removed.

## 3. What the data showed

1. **There are no exact duplicate rows anywhere** (0 removed in all 31 tables). The data's duplicates are business-level: for example 5,950 CRM records are flagged `duplicate_of_crm_id`, and the same person can hold a CRM record, a card, a loan and a deposit account under different keys. Finding them is the identity step's job, not curation's.
2. **One real data-quality defect: mixed date formats in `account_monthly_snapshot.month_end_date`.** 1,255,152 rows (10%) were not ISO: `YYYYMMDD` (418,081), `DD/MM/YYYY` (418,752) and `MM/DD/YYYY` (418,319). All are month-end dates, so the day is always above 12 and every one is unambiguous. They are recovered, so the column now has **0 NULLs**, every value is a true month-end, and the range is 2023-12-31 to 2026-06-30 (11 quarter-ends, matching the release README). The first build left these as NULL; this was found by checking `curated._cast_failures` and fixed (decision D9 in `documentation.md`).
3. **No other column failed to cast.** Every other typed column in every other table parsed cleanly. Messy formats elsewhere live in identity text columns (`dob_raw`, `*_phone_raw`, `*_name_raw`), which are handled by standardisation, not casting.
4. **CRM change-data fields:** `cdc_operation` is `U` for 916,466 rows, `I` for 101,419 and `D` for 2,115; `is_deleted` is true for those 2,115. `customers_current` therefore has 1,017,885 rows. Each CRM ID appears once in this release, so there is no version history to collapse. `effective_to_ts` is filled on every row; what value it holds was not examined.
5. **Snapshot tables:** `card_accounts`, `loan_accounts` and `deposit_accounts` each have one row per account in this release (`_current` equals the full table).
6. **CRM postal codes typed with the letter O instead of the digit 0** (`VOC2S8`, `L6X OZ4`): 1,887 rows. The first build rejected them and left `std_postal` empty. Found by checking the standardised columns against the CRM's own cleaned columns. The letter O in a digit position can only be a zero, so they are repaired, and the repair equals the CRM's clean `postal_code` in 1,887 of 1,887 cases. No postal code is now left empty when the raw one exists, in any of the four sources.

## 4. How the identity columns were validated

The standardise functions were checked against the real data, not just unit tests.

**How full the columns are** (share of rows with a value):

| Source | Name | DOB | DOB ambiguous in the text, then after resolution | Phone as full number | Phone masked | Postal |
|---|---:|---:|---:|---:|---:|---:|
| `customers` | 100% | 99.5% | 12.6% then 0% | 98.5% | 0% | 99.0% |
| `card_accounts` | 100% | 100% | **36.3%** then 0% | 38.8% | **58.2%** | 99.0% |
| `loan_accounts` | 100% | 99.0% | 0% | 98.0% | 0% | 99.0% |
| `deposit_accounts` | 100% | 98.0% | 0% | 96.0% | 0% | 99.0% |
| `external` (bureau) | 96.1% | 96.0% | 0% | none | none | 95.1% |

Consequences for matching: **card phone numbers are weak evidence** (58% are masked, keeping only a prefix and suffix). Date-of-birth ambiguity is settled before matching, so no identity table has an ambiguous date left: the CRM's 128,980 by its own cleaned `date_of_birth` (certain), the cards' 239,324 by the column's day-first convention (inferred from 398,847 day-first dates against 0 month-first). The reasoning and evidence are in [`ambiguous_values.md`](ambiguous_values.md).

**Checks against the CRM's own cleaned columns.** The CRM carries a cleaned version of several fields next to the raw one, which makes them ground truth for the standardiser: date of birth (0 disagreements with every unambiguous raw date, about 823,000 checks), phone (`std_phone_primary_e164` equals the CRM's `primary_phone_e164` in 1,004,777 of 1,004,777 rows) and postal code (equal in 1,009,780 of 1,009,780 rows, after the repair in section 3 item 6). The CRM raw columns are in several formats (`dob_raw` 3, phones 10, postal codes 16 including lower case); the cleaned columns are each in exactly one.

**Name parsing against ground truth.** `customers` has clean first and last name columns next to the raw name, so the parser could be scored: last name matches 99.5% of rows and first name 99.9%.

**The per-source guess about ALL-CAPS names, now tested.** For two-word ALL-CAPS names with no comma, whether word 1 or word 2 is the surname was checked against the set of surnames and given names in the CRM:

| Source | Result |
|---|---|
| `card_accounts` (262,897 names) | **first-last**: word 1 is a given name in 262,147 cases and a surname in 3,770 |
| `loan_accounts` (80,607 names) | **last-first**: word 1 is a surname in all 80,607 |

Both match the setting in `config/curate.yaml`. Two more findings: `deposit_accounts` names are a surname plus **initials only** (the first name is just an initial in 778,246 of 780,000 rows, and the surname is a known CRM surname in 99.8%), so deposit name evidence is surname plus initial. In `external`, 3.9% of rows have no name at all.

## 5. The standardised columns

| Field | Columns | Rule |
|---|---|---|
| Name | `std_name_first`, `_middle_initials`, `_last`, `_first_is_initial`, `_key` | Uppercase, accents stripped (the data is valid UTF-8; accents are real). A comma means last-first; trailing single letters mean last-first; ALL-CAPS two words follow the source's setting; otherwise left ambiguous. `_key` is the sorted token set, so word order does not matter |
| Date of birth | `std_dob`, `std_dob_alt`, `std_dob_ambiguous`, `std_dob_was_ambiguous`, `std_dob_basis` | Decided by a ladder, strongest evidence first: (1) only one reading possible (a part above 12, year-first formats, month names); (2) the source's own cleaned column when one exists (CRM `date_of_birth`, `std_dob_basis` = `source_clean_column`); (3) the column's own convention inferred from its one-way dates, only if at least 1,000 exist and the minority order is under 0.1% (cards, day-first, `convention`); (4) otherwise both readings are kept (`ambiguous`, `std_dob_alt` holds the other). `std_dob_was_ambiguous` says the text alone was ambiguous. A mixed column is never guessed. Impossible dates give NULL. Evidence per column: `curated._date_conventions` |
| Phone | `std_phone_<label>_e164`, `_masked`, `_prefix`, `_suffix` | `+1` plus ten digits. A masked phone has no full number, only its visible prefix and suffix. Phone alone never links records |
| Postal | `std_postal`, `std_fsa`, `std_postal_repaired` | `A1A1A1`, and the first three characters. The letter O in a digit position is repaired to 0 (the CRM has 1,887 of these, `std_postal_repaired` = true); any other invalid code stays empty. `external` takes the postal code from the end of the address |
| Employer | `std_employer` | Drops the `(City)` suffix and legal-form words so spelling variants collapse |

Every `std_*` column inherits the sensitivity label of the column it came from (for example `std_dob` is `direct_pii`), so a derived column is never less protected than its source.

## 6. Sensitivity labels after this step

`config/sensitivity.yaml` (generated): 104 `direct_pii`, 29 `quasi_identifier`, 12 `protected`, 1,350 `none`, 8 `unclassified` (restricted). The overdraft decision is applied (`overdraft_dpd` and `overdraft_dpd_bucket` are `none`). The proxy list is `contact_history.interpreter_used_flag` and `voice_samples.agent_accent_region`.

Still unclassified: the six Parquet `year` partition columns (derived from folder names, harmless but nobody has said so) and `transcript_json.content` and `.transcript_id` (the content is free text and should stay restricted). A person needs to decide the `year` columns.

## 7. Performance, and what is not understood

| | |
|---|---|
| Whole build | about 1.8 hours on the reference laptop |
| CPU use | about **1 core of 12**. Disk (3% busy) and memory (4 GB free) were not the limit, and DuckDB was set to all 12 threads. More cores will not help yet |
| Unexplained variance | `customers` took 2,469 s and `deposit_accounts` 1,947 s, but `external` (similar width and size) took 179 s and `loan_accounts` 82 s. The cast-failure scan is fast (about 2 s per 400k rows) and the Python identity functions cost only a few minutes per million rows, so **neither explains the slow tables**. The cause was not found. A suspect is the duplicate-removal step over very wide rows; this is **unproven** |

## 8. Not done / open

- **Enum validation against `code_lookups`** (plan section 3.2) is not implemented.
- **`text_controls.py`** (which notes and transcripts are eligible for downstream LLM use) is not implemented.
- **`mapping_assistant.py`** (suggest mappings for brand-new tables) is not implemented.
- **`verify_warehouse.py` for curated** compares the builder's own log to the tables, so it proves consistency, not that the cleaning is right. Section 4 is the evidence on correctness.
- **Cards: 8.6%** of parsed last names are not a known CRM surname (likely hyphenated or compound names). Not investigated.
- **`transcript_json`** is loaded raw but not parsed into turns.
- Speed-up of the build is **not started**.
