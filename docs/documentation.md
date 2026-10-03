# TrustIssues: Living Documentation

> **This file is updated in the same change as the code it describes.** Every slice that lands adds or edits its section here, plus a line in the [Changelog](#changelog). If code and this file disagree, the code is right and this file is a bug.
>
> Plan of record: [`implementation_plan.md`](implementation_plan.md). This file records what is *actually built*, how to run it, and what we learned from the data.

## Contents
1. [Status](#1-status)
2. [Quick start](#2-quick-start)
3. [Architecture so far](#3-architecture-so-far)
4. [Layer 1 components](#4-layer-1-components)
5. [Dynamic behaviour: what happens when new data arrives](#5-dynamic-behaviour)
6. [Data findings](#6-data-findings)
7. [Decisions and deviations](#7-decisions-and-deviations)
8. [Changelog](#changelog)

---

## 1. Status

Layer 1, Data Product Factory (owner P1). Legend: ✅ done and tested · 🟡 built, not fully verified · ⬜ not started.

| Plan § | Component | Status |
|---|---|---|
| 3.0 | `common/config.py`, `common/db.py` | ✅ |
| 3.1 | `ingest/load_raw.py` (CSV, Parquet, transcript JSON) | 🟡 full load running (CSV tables done, large Parquet tables in progress) |
| 3.1 | `ingest/manifest.py` | ✅ |
| 3.1 | `ingest/schema_drift.py` | ✅ |
| 3.1 | `ingest/dictionary.py` → `config/sensitivity.yaml` | ✅ re-run on the real data after curation: overdraft decision applied, `std_*` columns inherit labels (3 unit tests), proxy false positive gone |
| 3.2 | `curate/standardise.py` | ✅ 18 unit tests |
| 3.2 | `curate/build_curated.py` + `config/curate.yaml` (generic, replaces per-table `sql/*.sql`, see D6) | ✅ **Complete on the real data: all 31 tables**, verified by `scripts/verify_warehouse.py`. 0 exact duplicates; the one real defect (mixed date formats, 10% of `account_monthly_snapshot`) is recovered. Two bugs found and fixed on the way (D8, D9). Full report: [`curation.md`](curation.md) |
| 3.2 | `curate/text_controls.py`, `mapping_assistant.py` | ⬜ |
| 3.3 | identity (`nodes`, `rules`, `splink_model`, `second_opinion`, `review_queue`, `cluster`, `relationships`) | ⬜ |
| 3.4 | gold (`survivorship`, `build_c360`, views, `publish`) | ⬜ |
| 3.5 | `quality/dq_report.py` + `config/dq_checks.yaml`: data quality report (`reports/data_quality_report.md`), `dq_*` flag columns, derived columns, `curated.dq_orphan_customer_refs` | ✅ run on the real data: 53 checks with records, 20 clean checks, 0 errors; 6 unit tests. **The plan assigns `dq_report.py` to P2: coordinate before anyone rewrites it** |
| 3.5 | `quality/contract_checks.py`, `quality/anomaly.py` | ⬜ |
| 3.6 | `pipeline.py` (runs ingest, dictionary, curate, labels with timings) | 🟡 written; each stage has been run on the real data separately, the single command has not been run end to end |
| 3.6 | `contracts/c360_customer.yaml` | ⬜ |
| — | `scripts/verify_warehouse.py` (row counts vs the release, curated reconciliation) | ✅ run on the real database: all 31 raw tables and all 31 curated tables pass |
| — | `requirements.md` / `requirements.txt` (rebuild on your own machine) | ✅ |

## 2. Quick start

Full setup from nothing (download, unzip, disk and RAM needs, troubleshooting): [`../requirements.md`](../requirements.md).

```bash
python -m trustissues.factory.pipeline                   # everything below, in order, with timings
python scripts/verify_warehouse.py                       # confirm the result is correct
pip install -e ".[dev]"                                  # installs the trustissues package
python -m trustissues.factory.ingest.load_raw            # raw load (re-runnable; only new/changed files are read)
python -m trustissues.factory.ingest.load_raw --only customers agents   # a subset
python -m trustissues.factory.ingest.dictionary          # data dictionary + config/sensitivity.yaml
python -m trustissues.factory.curate.build_curated       # raw -> curated (typed, deduped, std_* identity columns)
python -m trustissues.factory.quality.dq_report          # dq flags, derived columns, reports/data_quality_report.md (about 20 s)
python -m trustissues.factory.curate.build_curated --only customers card_accounts
python -m pytest tests -q
```

Where things live:

| What | Where |
|---|---|
| Warehouse | `warehouse/trustissues.duckdb` (gitignored) |
| Source data | `maple_data/maple_collections_release/` (gitignored). Override with env `MAPLE_DATA_DIR` |
| Settings | `config/settings.yaml` (`as_of_date: 2026-09-28`, thresholds) |

DuckDB allows **one writer process at a time**. Do not run two loads, or a load and the dictionary step, together.

## 3. Architecture so far

```
maple_data/ (31 tables: CSV, Parquet by year, transcript JSON)
      │  load_raw.py  (all text, + _ingested_at _source_file _batch_id _row_hash)
      ▼
   raw.*  ──── raw._load_manifest, raw._schema_drift, raw._dictionary, raw._relationships
      │  build_curated.py  (types from schema.json, dedupe, std_* identity columns)
      ▼
 curated.* ──── curated._cast_failures, curated._build_log, curated.<t>_current
      │  (next) identity  →  gold.* (c360_customer, c360_account, views)
```

Schemas: `raw` is as received. `curated` is typed, cleaned and deduplicated. `gold` is the only thing consumers read.

## 4. Layer 1 components

### 4.1 `common/config.py`
Loads `config/settings.yaml`. Exposes `AS_OF_DATE` (a `date`), `maple_data_dir()`, `warehouse_path()`. Code never calls `today()`.

### 4.2 `common/db.py`
`connect(read_only=False)` opens the warehouse and creates the `raw`, `curated`, `gold` schemas (not when read-only).

### 4.3 `ingest/load_raw.py`
- **Discovery:** every `data/*.csv` is a source, and so is every `data/<dir>/year=*/*.parquet` folder. No hard-coded table list.
- **Everything is text.** Raw means as received, and this keeps leading-zero IDs (the 10-digit CIF). Typing happens in curated.
- **Metadata per row:** `_ingested_at`, `_source_file`, `_batch_id` (`B<UTC timestamp>`), `_row_hash` (md5 of all source columns).
- **Incremental:** unchanged files are skipped via the manifest. For a changed file, rows whose `_row_hash` already exists from an earlier batch are skipped. Exact duplicates *within* a batch are kept on purpose; curated removes them, and the DQ report counts them.
- **New columns** in a later extract widen the table (`ALTER TABLE ADD COLUMN`); older rows get null.
- **Transcripts:** `raw.transcript_json` has one row per JSON file with the content verbatim.
- Exit code 1 if any source is `BLOCKED` by schema drift.

### 4.4 `ingest/manifest.py`
`raw._load_manifest(source, file, bytes, mtime, sha256, row_count, columns, batch_id, loaded_at)`. A file is re-read only if it is new or its sha256 changed (size and mtime are checked first so unchanged files are not re-hashed).

### 4.5 `ingest/schema_drift.py`
| Situation | Result |
|---|---|
| Registered column missing | **Blocks** that source. Reports a likely rename by name similarity (`channel` → `channel_v2`) |
| Unknown column present | Warns, loads it, logs it |
| Source not registered | Loads, nothing to compare |

Events go to `raw._schema_drift`. Planned: LLM-ranked rename suggestions.

### 4.6 `ingest/dictionary.py`
Reads `DATA_DICTIONARY.xlsx` into `raw._dictionary` (columns), `raw._dictionary_tables`, `raw._relationships`. Generates `config/sensitivity.yaml` with each column as `direct_pii`, `quasi_identifier`, `protected`, `none` or `unclassified`.
- **Default-deny.** A loaded column the dictionary does not classify is `unclassified` and treated as restricted. Only explicit `none` columns may be allow-listed into the decisioning view.
- Proxy columns (age, accent, interpreter use) are detected by name, with exclusions for "ageing", "language" and similar.
- `config/sensitivity.yaml` is generated. Never edit it by hand.
- **Human decisions** go in `config/sensitivity_overrides.yaml` (reviewed, each with a reason, who decided and a date). Applied on top of the dictionary on every run. Currently: `deposit_accounts.overdraft_dpd` and `overdraft_dpd_bucket` are `none` (decided 2026-10-03).
- **Derived columns inherit.** Curated `std_*` columns take the label of the column they come from (standardised DOB is `direct_pii` like the raw DOB; `std_postal` and `std_fsa` are `quasi_identifier`), and `<col>__raw` takes the label of `<col>`. A derived column with an unknown source is `unclassified`.

### 4.7 `curate/standardise.py`
Pure functions (no DB), the single place where messy identity fields become comparable. Principle: **never invent information**. When a value is ambiguous we keep both readings or leave the field empty.

| Function | Input examples seen in the data | Output |
|---|---|---|
| `parse_dob` | `1975-09-12`, `1975/09/12`, `19750912`, `Feb 23 2008`, `13-01-1990`, `07/12/1992` | `dob_iso`, `dob_alt_iso`, `dob_ambiguous`. Year-first and month-name forms are unambiguous. A day above 12 resolves the order. Both parts ≤ 12 and different keeps **both** readings (`/` ranked month-first, `-` and `.` day-first). Invalid dates (31/02) give null |
| `parse_phone` | `6045336260`, `(416) 373-5940`, `236.749.7421`, `+1 (438) 988 9802`, `450672xxx81` | `phone_e164` (`+1…`). A masked phone has **no** e164; its visible `phone_prefix` and `phone_suffix` are kept. Invalid NANP numbers give null |
| `parse_name` | `Ariel I Kline`, `Ball, Tracy`, `J. LANGLOIS`, `DYER D C`, `NOWAK, OKSANA ZOFIA` | `first`, `middle_initials`, `last`, `first_is_initial`, `name_key` (sorted token set, order-insensitive). Uppercase, accents stripped. A comma always means last-first. Trailing single letters mean last-first. An ALL-CAPS two-word name is decided only by the source's `caps_default`, else left ambiguous (`last` null, `name_key` still set) |
| `parse_postal` | `N5Y2A2`, `M5V 2T6` | `postal` (`A1A1A1`), `fsa`. Rejects letters Canada never uses in the first position (D F I O Q U) |
| `normalise_employer` | `Wildrose Machining Corp.` / `... Limited`, `Silverbirch Media Inc. (Saguenay)` | Drops the `(City)` suffix and legal-form words, so spelling variants collapse to one key |

### 4.8 `curate/build_curated.py` and `config/curate.yaml`
Builds `curated.<table>` from `raw.<table>` for **every** table in `raw`, with no per-table SQL needed.
- **Types** come from `schema.json` (`int`→BIGINT, `decimal`→DOUBLE, `date`, `timestamp`, `boolean`; strings, enums, JSON and arrays stay text). Blank text becomes NULL. A table with no schema entry passes through as text.
- **Failed casts** become NULL, are counted in `curated._cast_failures` (table, column, rows, an example), and the original text is kept in `<col>__raw` on that row. Only columns that actually fail get a `__raw` column.
- **Duplicates** are removed on `_row_hash` (earliest ingest wins). Counts are in `curated._build_log` (`raw_rows`, `curated_rows`, `dupes_removed`).
- **Identity sources** (`customers`, `card_accounts`, `loan_accounts`, `deposit_accounts`, `external`), listed in `config/curate.yaml`, get `std_*` columns: `std_name_{first,middle_initials,last,first_is_initial,key}`, `std_dob`, `std_dob_alt`, `std_dob_ambiguous`, `std_phone_<label>_{e164,masked,prefix,suffix}`, `std_postal`, `std_fsa`, `std_employer`. For `external` the postal code is the trailing token of `subject_address_reported`.
- **History is kept.** `curated.<table>_current` (customers, card, loan, deposit) holds one row per key: latest `snapshot_date`, or the latest non-deleted CRM version.
- Not yet done in this slice: enum validation against `code_lookups`, and `text_controls.py` (note and transcript eligibility).

## 5. Dynamic behaviour

What happens when new data arrives. "Auto" means no human step.

| New data | Behaviour |
|---|---|
| More rows or a new file for a known table | **Auto.** Only new or changed files are read; rows already loaded are skipped |
| A new column | **Auto, restricted.** Loaded, logged, `unclassified` until someone classifies it |
| A removed or renamed column | **Blocked** with a rename hint until a person approves |
| A brand-new table | **Lands in `raw` and in `curated` automatically** (as text, since there is no schema entry). Reaches gold only after a reviewed mapping (`mapping_assistant.py`, not yet built) |

Covered by `tests/test_ingest_dynamic.py` and `tests/test_build_curated.py` (an unregistered table passes through as text). The curated and gold stages are not built yet, so end-to-end "new data becomes golden" is **not yet demonstrated**. It will be tested once `pipeline.py` exists.

## 6. Data findings

The curated layer's findings (no exact duplicates, the mixed date formats, how well each identity field parses) are in [`curation.md`](curation.md). The notes below predate it.

**Source of truth:** the dictionary and schema shipped inside `maple_data/maple_collections_release/` (`DATA_DICTIONARY.xlsx`, `schema/schema.json`) and the data files themselves. The `docs/CIBC Hackathon - Data Dictionary.xlsx` is an ideation-phase reference only: it is never read by the code. It differs from the release in `benchmark_questions` only (it has 5 extra grader-side columns).

**What "classified" means.** The dictionary's `Sensitivity` column gives each column one label: `Direct PII` (38 columns, e.g. names, DOB, phone, email, address, national-ID hash, free-text notes and transcripts), `Quasi-identifier` (20, e.g. postal code, FSA, city, age, age band, geo coordinates, employer name), `Protected attribute` (12: gender, marital status, citizenship, household size, dependants, newcomer flag and arrival date, accessibility flag and type, vulnerability type, `ccb_deposit_flag`, customer accent region) or blank. Blank is read as `none` (1,347 columns). A column the dictionary does not list is `unclassified` and restricted.

**Known gaps in the classification (to resolve before the decisioning view is built):**
1. A blank label does not mean "safe to score on". Some blank-labelled columns are sensitive in practice (for example `customers.vulnerability_flag`, `fraud_victim_flag`, `military_or_reserve_flag`, `preferred_language`, consent and cease-contact flags). The decisioning view must therefore be an explicit, reviewed **allow-list**, not "everything labelled none".
2. `config/sensitivity.yaml` covers raw columns only. The curated `std_*` and `*__raw` columns derive from PII and quasi-identifier columns and are not yet labelled, so they must inherit the label of their source column.
3. `deposit_accounts.overdraft_dpd` and `overdraft_dpd_bucket` are unclassified (restricted) until a person decides.

Facts learned from the real data, recorded as we find them.

- Dictionary and `schema.json` disagree (checked by comparing both lists). The dictionary **lacks** `deposit_accounts.overdraft_dpd` and `overdraft_dpd_bucket`, which exist in the files and in `schema.json` (the release README says they were added later). Those two are therefore `unclassified` (restricted) until someone classifies them. The dictionary has 10 entries that `schema.json` does not: the transcript JSON fields (`turns`, `turn_count`, `turn.*`), which live inside the JSON files, not in a CSV. Raw column lists come from the files themselves.
- Raw load verified: all 31 tables match the row counts in `schema.json`; no blocking schema drift; 25,000 transcript JSON files loaded.
- The proxy-column check flagged `external.avg_age_of_trades_months` (age of credit accounts, not of a person). The pattern now excludes `avg_age` and `age_of`.
- Customer keys differ per system (`crm_customer_id`, card/loan `src_customer_ref`, 10-digit CIF in deposit/external/bureau). No shared ID.
- `customers` carries `*_raw` and cleaned variants side by side (`dob_raw` / `date_of_birth`, `primary_phone_raw` / `primary_phone_e164`, `postal_code_raw` / `postal_code`), but other systems carry only `*_raw` (`cardholder_dob_raw`, `borrower_phone_raw`, `holder_postal_raw`, `subject_dob_reported`).
- **Formats differ by source system** (sampled from the files):

  | Field | CRM `customers` | Cards | Loans | Deposits | Bureau `external` |
  |---|---|---|---|---|---|
  | Name | `Ariel I Kline`, `Ball, Tracy` | `J. LANGLOIS`, `JESSICA SMIRNOV` (caps, first-last) | `Guillaume Charron`, `KAPOOR CASEY` (caps, last-first) | `DYER D C` (last + initials) | `NOWAK, OKSANA ZOFIA` |
  | DOB | ISO, `07/12/1992`, `Feb 23 2008` | `13-01-1990` (DD-MM-YYYY) | `1979/10/22` | `19940228` | ISO |
  | Phone | digits, `(416) 373-5940`, dotted | digits or masked `450672xxx81` | `+1 (438) 988 9802` | digits | none |
  | Postal | spaced or not | no space | spaced | spaced | trailing token of the address |

  The ALL-CAPS name orders (cards first-last, loans last-first) are **inferred from samples** and set in `config/curate.yaml`. To be confirmed by checking agreement with national-ID matches once identity runs.
- **Encoding is fine.** The files are valid UTF-8 with no broken characters (checked: 0 occurrences of the replacement character U+FFFD in the customer text columns). 77,262 customers have genuine accented letters (`Frédérique`, stored as the correct bytes) and 77,386 employer names do too. An earlier version of this note claimed names contained broken characters; that was wrong, a display problem in the Windows console. Accents are stripped only in the matching columns (`std_name_*`).
- Raw load throughput is dominated by the row hash over wide tables (`contact_history` 2.7M rows took ~3 min).

*Open questions from plan §11 are tracked in [§7](#7-decisions-and-deviations) until answered.*

## 7. Decisions and deviations

| # | Decision | Why |
|---|---|---|
| D1 | Raw columns are all text | "As received"; preserves leading zeros; typing is curated's job |
| D2 | Duplicates within a batch are kept in raw | Curated dedupes on `_row_hash`; the DQ report must be able to count them |
| D3 | Sources discovered from the folder, not listed in code | New data must not need a code change |
| D4 | Unclassified columns are restricted by default | A new column must never reach a model unreviewed |
| D5 | Rename suggestions use name similarity for now | LLM wrapper (`common/llm.py`) is not built; plan assigns LLM ranking later |
| D6 | Curated is built **generically from `schema.json`** plus `config/curate.yaml`, instead of one hand-written `sql/<table>.sql` per table (plan §3.2) | Keeps the system dynamic: a new table is typed and deduped with no code change. Per-table SQL can still be added where a table needs special logic |
| D8 | The standardise functions receive their inputs cast to text | The full-data run crashed on `external`: `subject_dob_reported` is a DATE in `schema.json`, and the DOB function only accepted text (`No function matches std_dob(DATE)`). The four earlier identity tables use text columns, so they passed. A regression test now reproduces the exact error on the old code |
| D9 | Date columns fall back to non-ISO formats, but only when exactly one reading is valid | `account_monthly_snapshot.month_end_date` came in as ISO, `YYYYMMDD`, `DD/MM/YYYY` and `MM/DD/YYYY`; 1,255,152 rows (10%) were NULL after the first build. All are month-end dates, so the day is always above 12 and the reading is certain. Ambiguous values (both parts 12 or less) still stay NULL with their text kept. Recoveries are counted in `curated._format_recoveries` |
| D10 | Data-quality handling is applied as **new columns and flags**, never by overwriting a value, and by an idempotent step (`dq_report`) that can be re-run | A repair the data cannot justify (shifted bureau columns, masked phones, bucket vs dpd) must not be guessed. Flags let downstream code exclude rows; the report is generated from query results so its numbers cannot drift |
| D7 | Ambiguous dates keep both readings; ambiguous ALL-CAPS names stay unparsed unless the source sets a convention | Never invent identity evidence; wrong merges are worse than missed ones |

## Changelog

| Date | Slice | Change |
|---|---|---|
| 2026-10-03 | Layer 1 · quality | **Data quality report.** 53 issues quantified (6 known incidents verified against the data; formats, broken references, missing values, contradictions), 20 clean checks. Handling applied: `channel_resolved`, `cif10`, `salutation_std`, 12 `dq_*` flag columns, `curated.dq_orphan_customer_refs`. Corrected an earlier false claim that names contain broken characters (the data is clean UTF-8). Fixed a proxy-column false positive (`dq_trade_age_inconsistent`). 49 tests |
| 2026-10-03 | Layer 1 · curate | **Curation complete on all 31 tables.** Found and fixed mixed date formats in `account_monthly_snapshot.month_end_date` (D9, 35 tests). Validated the standardised identity columns against the data (name parse 99.5% / 99.9%, ALL-CAPS name order confirmed for cards and loans). Wrote `curation.md` |
| 2026-10-03 | Layer 1 · curate | First full-data run: 16 tables OK, then crash on `external` (DATE-typed identity column). Fixed (D8) and added a regression test (30 tests total). Documented that the curated build is slow and single-core (requirements.md section 4a) |
| 2026-10-03 | Layer 1 · tooling | Added `pipeline.py`, `scripts/verify_warehouse.py`, `requirements.md`, `requirements.txt`, `config/sensitivity_overrides.yaml`, label inheritance (3 tests). Merged teammate's `.gitignore`. Aligned setup docs with the updated plan (voice pack, shared DuckDB) |
| 2026-10-03 | Layer 1 · curate | Added `standardise.py` (18 tests), `build_curated.py`, `config/curate.yaml`, 2 builder tests. Fixed: Parquet `year` partition column no longer reported as schema drift. Documented source formats |
| 2026-10-03 | Layer 1 · ingest | Added config, db, `load_raw`, `manifest`, `schema_drift`, `dictionary`, 5 tests. Created this file |
