# Data Quality Report

Maple Bank collections data, snapshot **2026-09-28**. Generated from the warehouse by `python -m trustissues.factory.quality.dq_report`; every number below is a query result, and the checks are defined in `config/dq_checks.yaml`.

## 1. Summary

- **56 issues found** across the 31 tables, plus 2 context events from the operations log that are not data defects.
- **23 further checks found nothing** (section 11), so the coverage is visible.
- **No exact duplicate rows exist anywhere.** The duplicates in this data are the same *person* stored under different IDs (section 5), not copied rows.
- How each issue was handled: **flagged** 19, **documented** 18, **standardised** 12, **fixed** 5, **deferred to identity layer** 1, **excluded from current view** 1.
- **Schema drift:** the loader recorded 0 event(s), 0 blocking. The one rename in this release (K1, `channel` to `channel_v2`) was already registered in the release's schema, so it appears as a data issue, not as drift.
- **Nothing was deleted or overwritten.** Original columns stay as received in `raw.*` and in the same columns of `curated.*`. Fixes are new columns, new flags, or recovered values with the original text kept.

**How to read the handling column**

| Status | Meaning |
|---|---|
| Fixed | The value is repaired or resolved with certainty, in the data |
| Standardised | A clean `std_*` or derived column holds one format; the original is kept |
| Flagged | The problem cannot be repaired. A `dq_*` flag column marks the rows so downstream code can exclude or discount them |
| Documented | Not changed and not flaggable per row; downstream users are told what to do |
| Deferred | Handled in a later layer (customer identity), not in curation |
| Excluded | Kept in the full table, left out of the one-row-per-key `_current` table |
| Context | A real-world event, not a data defect |

## 2. Issues by size and severity

| ID | Issue | Records affected | Share of table | Severity | Handling |
|---|---|---:|---:|---|---|
| F1 | Month-end date written in four formats in `account_monthly_snapshot.month_end_date` (ISO, YYYYMMDD, DD/MM/YYYY, MM/DD/YYYY) | 1,255,152 | 9.99% | high | Fixed |
| F9 | Card phone numbers masked (`450672xxx81`): digits missing | 384,422 | 58.2% | high | Flagged |
| R6 | Account snapshot points to a CRM customer that does not exist | 328,891 | 2.62% | high | Flagged |
| F12 | Deposit customer key lost its leading zeros (7 or 8 digits instead of the 10-digit CIF) | 156,837 | 20.1% | high | Fixed |
| T1 | Collections cases change abruptly from April 2026: new cases surge, cures barely grow, and the 30-day cure rate falls | 147,733 | 40.2% | high | Documented |
| T2 | Promise break rate rises from May 2026, mostly because fewer promises are for the full overdue amount | 76,727 | 37.5% | high | Documented |
| R2 | Contact record points to a CRM customer that does not exist | 75,500 | 2.81% | high | Flagged |
| R10 | Same person stored as more than one CRM record (identical national-ID hash) | 36,992 | 3.63% | high | Deferred to identity layer |
| K1 | Contact channel renamed upstream: `channel` is empty and the value is in `channel_v2` | 19,463 | 55.1% | high | Fixed |
| R1 | Collections case points to a CRM customer that does not exist | 10,365 | 2.82% | high | Flagged |
| R3 | Promise to pay points to a CRM customer that does not exist | 6,744 | 2.82% | high | Flagged |
| F14 | Deposit first name is only an initial (`DYER D C`) | 778,246 | 99.8% | medium | Standardised |
| F3 | Card date of birth in DD-MM-YYYY (every filled value) | 660,000 | 100.0% | medium | Standardised |
| F2 | CRM date of birth not in ISO format (`dob_raw`: ISO, MM/DD/YYYY, `Mon DD YYYY`) | 405,432 | 39.7% | medium | Standardised |
| F13 | Card first name is only an initial (`J. LANGLOIS`) | 329,786 | 50.0% | medium | Standardised |
| F15 | ALL-CAPS two-word card names with no comma: word order not stated (`JESSICA SMIRNOV`) | 262,897 | 39.8% | medium | Standardised |
| F7 | Card date of birth where day and month are both 12 or less, so the text alone allows two readings | 239,324 | 36.3% | medium | Standardised |
| F6 | CRM date of birth where day and month are both 12 or less, so the text alone allows two readings | 128,980 | 12.6% | medium | Fixed |
| F16 | ALL-CAPS two-word loan names with no comma: word order not stated (`KAPOOR CASEY`) | 80,607 | 19.9% | medium | Standardised |
| R9 | Routing rule used in assignments is missing from the reference table (RR-EARLY-03, RR-MID-03, RR-LATE-03, RR-REC-03) | 74,697 | 24.0% | medium | Documented |
| M6 | Card days-past-due missing | 52,734 | 7.99% | medium | Documented |
| L8 | Bureau: oldest trade is younger than newest trade (impossible) | 37,362 | 3.74% | medium | Flagged |
| M7 | Loan days-past-due missing | 32,179 | 7.96% | medium | Documented |
| R7 | Contact record points to a collections case that does not exist | 26,864 | 1.00% | medium | Documented |
| K2 | Predictive dialer outage, 23 Sep 08:00 to 24 Sep 21:00: about 60% fewer outbound calls | 25,472 | 0.95% | medium | Flagged |
| L1 | Card delinquency bucket disagrees with days past due (e.g. dpd 0 but bucket 1-30, or one band too high) | 24,205 | 3.67% | medium | Flagged |
| R4 | Agent note points to a CRM customer that does not exist | 21,592 | 2.84% | medium | Flagged |
| M5 | Bureau record says `hit` but has no risk score | 18,937 | 1.89% | medium | Flagged |
| L2 | Loan delinquency bucket disagrees with days past due | 14,769 | 3.65% | medium | Flagged |
| T4 | Every promise for 90% to 99.9% of the overdue amount was broken, while promises for the full amount almost never are | 11,277 | 5.52% | medium | Documented |
| K4 | Bureau file converted with a shifted column for some rows: inquiry counts impossible (3 months above 6 months, or 6 above 12) | 9,918 | 0.99% | medium | Flagged |
| F18 | Deposit balances in two currencies (7,780 USD accounts among CAD) with no exchange-rate field in the data | 7,780 | 1.00% | medium | Flagged |
| T3 | Outcomes are recorded late: case cures stop before the snapshot date, and some promises stay open after their grace period | 5,286 | 16.0% | medium | Documented |
| L3 | Case bucket disagrees with current days past due (one band higher than dpd implies) | 3,807 | 1.04% | medium | Flagged |
| L7 | Promise marked kept but the amount paid is below the promised amount | 2,844 | 1.19% | medium | Flagged |
| L11 | Hardship offer, or the customer's answer to one, dated after the snapshot date | 1,757 | 4.31% | medium | Documented |
| R5 | Call transcript points to a CRM customer that does not exist | 695 | 2.78% | medium | Flagged |
| K3 | App outage 14 Sep: payments not posted, late fees charged in error; late-fee disputes about 3x baseline 15-22 Sep | 31 | 4.29% | medium | Documented |
| F8 | CRM phone numbers in 10 different formats | 1,004,777 | 98.5% | low | Standardised |
| F5 | Deposit date of birth in YYYYMMDD (every filled value) | 764,244 | 98.0% | low | Standardised |
| F10 | CRM postal code written with and without a space, in lower case, or both | 737,153 | 72.3% | low | Standardised |
| F4 | Loan date of birth in YYYY/MM/DD (every filled value) | 400,468 | 99.0% | low | Standardised |
| F17 | Customer salutation spelled several ways (`Mr.`, `mr`, `MR.`; 20 spellings of 7 values) | 128,680 | 12.6% | low | Standardised |
| M4 | Bureau date of birth blank | 39,543 | 3.95% | low | Documented |
| L5 | Right-party contacts exceed total contact attempts on a case | 27,680 | 7.54% | low | Flagged |
| M3 | Deposit date of birth blank | 15,756 | 2.02% | low | Documented |
| L6 | Case promise counts do not add up (kept + broken + open is not total) | 8,747 | 2.38% | low | Flagged |
| L4 | Deposit overdraft used exceeds the overdraft limit | 6,343 | 0.81% | low | Flagged |
| M1 | CRM date of birth blank | 5,026 | 0.49% | low | Documented |
| M2 | Loan date of birth blank | 4,032 | 1.00% | low | Documented |
| R11 | CRM record marked deleted (change-data operation D) | 2,115 | 0.21% | low | Excluded from current view |
| F11 | CRM postal code typed with the letter O where the digit 0 belongs (`VOC2S8`, `L6X OZ4`) | 1,887 | 0.18% | low | Fixed |
| L10 | Card utilisation outside 0 to 1.5 | 33 | 0.01% | low | Documented |
| L9 | Bureau: file-since date is after the pull date | 17 | 0.00% | low | Documented |
| M8 | CRM employer name blank | 359,829 | 35.3% | info | Documented |
| R8 | Collections case with no assignment history | 86,725 | 23.6% | info | Documented |

## 3. Known incidents (from the operations log)

The operations log (`ops_events`) lists six events. Each was looked for in the data. "Reported" is the record count the operations team gave; "Found" is what this report measured.

### K1. Contact channel renamed upstream: `channel` is empty and the value is in `channel_v2`

**Reported:** 0 · **Found:** 19,463 of 35,322 (55.1%) · **Event:** REL-0926 · **Tables:** `contact_history` · **Severity:** high

*Evidence:* channel_v2 first appears on 2026-09-26; the two columns never disagree (0 conflicts); after COALESCE 0 rows have no channel.

**Handling (fixed).** New column `channel_resolved` = COALESCE(channel_v2, channel). Downstream code must use it, not `channel`. Total column is rows on or after 26 Sep 2026.

### K2. Predictive dialer outage, 23 Sep 08:00 to 24 Sep 21:00: about 60% fewer outbound calls

**Reported:** 35,000 · **Found:** 25,472 of 2,688,337 (0.95%) · **Event:** INC-0923 · **Tables:** `contact_history` · **Severity:** medium

*Evidence:* outbound calls per day: median 6,613 for 14-22 Sep, then 3,044 on 23 Sep and 3,126 on 24 Sep.

**Handling (flagged).** Values are real, so nothing is changed. Rows inside the window are flagged `dq_dialer_outage_window` so features do not read the drop in calls as a change in customer behaviour (the incident report says it is not). The reported 35,000 is not the number of contact rows in the window (found: see above) and cannot be reproduced from the data; the two figures probably measure different things (for example calls that would have been attempted).

### K3. App outage 14 Sep: payments not posted, late fees charged in error; late-fee disputes about 3x baseline 15-22 Sep

**Reported:** 1,900 · **Found:** 31 of 723 (4.29%) · **Event:** INC-0914 · **Tables:** `collections_cases`, `card_statements` · **Severity:** medium

*Evidence:* late-fee disputes per day 15-22 Sep: 3.9 against a baseline of 1.4 (2.7x). The share of statements due 14 Sep with a fee is normal, so the roughly 1,900 accounts charged in error cannot be identified in this release; no fee reversals are posted yet.

**Handling (documented).** Not changed and not flaggable per account (the data does not identify them). Documented: treat late-fee disputes dated 15-22 Sep 2026 as inflated when computing dispute baselines. Affected column counts the late-fee disputes inside that window.

### K4. Bureau file converted with a shifted column for some rows: inquiry counts impossible (3 months above 6 months, or 6 above 12)

**Reported:** 10,000 · **Found:** 9,918 of 1,000,000 (0.99%) · **Event:** DFI-0812 · **Tables:** `external` · **Severity:** medium

*Evidence:* the rate is about 1.0% in every monthly bureau file (0.5% to 1.8%), so the bad rows are scattered through the data, not one bad load.

**Handling (flagged).** The correct values cannot be recovered. Rows are flagged `dq_inquiries_inconsistent`; the inquiry columns of flagged rows must not be used as features. A rule on these three fields finds the large majority of the reported records but not necessarily all of them.

### K5. Regional employer layoffs (Windsor-Essex): more hardship and job-loss in one region

**Reported:** 4,000 · **Related records in the data:** 13,664 of 1,020,000 · **Event:** ECO-0715 · **Tables:** `customers` · **Severity:** info

**Handling (context, not a defect).** Not a data defect. A real change in customer behaviour from 15 Jul to 28 Sep 2026. Customers in Windsor are counted (all of them, since the data does not mark who was laid off). Models trained across this period will see a regional shift.

### K6. Hardship policy v4.2 from 1 Mar 2026: reduced-payment programme changed to 50% for 3 months

**Reported:** 0 · **Related records in the data:** 18 of 18 · **Event:** POL-0301 · **Tables:** `hardship_programs`, `offers` · **Severity:** info

**Handling (context, not a defect).** Not a data defect. A policy change that means older offers and programmes follow the previous version; policy search must pick the version in force on the as-of date. Affected column counts programme rows.

## 4. Inconsistent formats

Every source system writes dates of birth, phones, postal codes and names in its own format. The original columns still hold those formats; the `std_*` columns are uniform.

### F1. Month-end date written in four formats in `account_monthly_snapshot.month_end_date` (ISO, YYYYMMDD, DD/MM/YYYY, MM/DD/YYYY)

**Affected:** 1,255,152 of 12,569,692 (9.99%) · **Tables:** `account_monthly_snapshot` · **Severity:** high

**Handling (fixed).** Recovered when exactly one reading is valid (every value is a month-end, so the day is always above 12). 0 NULLs remain; the original text of any unrecoverable value would be kept in `month_end_date__raw`.

### F2. CRM date of birth not in ISO format (`dob_raw`: ISO, MM/DD/YYYY, `Mon DD YYYY`)

**Affected:** 405,432 of 1,020,000 (39.7%) · **Tables:** `customers` · **Severity:** medium

**Handling (standardised).** Parsed into `std_dob`. Original `dob_raw` is kept unchanged.

### F3. Card date of birth in DD-MM-YYYY (every filled value)

**Affected:** 660,000 of 660,000 (100.0%) · **Tables:** `card_accounts` · **Severity:** medium

**Handling (standardised).** Parsed into `std_dob`.

### F4. Loan date of birth in YYYY/MM/DD (every filled value)

**Affected:** 400,468 of 404,500 (99.0%) · **Tables:** `loan_accounts` · **Severity:** low

**Handling (standardised).** Parsed into `std_dob`.

### F5. Deposit date of birth in YYYYMMDD (every filled value)

**Affected:** 764,244 of 780,000 (98.0%) · **Tables:** `deposit_accounts` · **Severity:** low

**Handling (standardised).** Parsed into `std_dob`.

### F6. CRM date of birth where day and month are both 12 or less, so the text alone allows two readings

**Affected:** 128,980 of 1,020,000 (12.6%) · **Tables:** `customers` · **Severity:** medium

*Evidence:* settled by the CRM column date_of_birth: 213,884 of 213,884 unambiguous slash dates in dob_raw agree with it, 0 disagree.

**Handling (fixed).** Settled with certainty by the CRM's own cleaned `date_of_birth`, which agrees with every unambiguous `dob_raw`. `std_dob_basis` = `source_clean_column`. The text column alone is mixed (day-first and month-first both occur), so a column-wide rule would have been wrong for 43% (if day-first) or 57% (if month-first) of these. A record with no clean value would keep both readings.

### F7. Card date of birth where day and month are both 12 or less, so the text alone allows two readings

**Affected:** 239,324 of 660,000 (36.3%) · **Tables:** `card_accounts` · **Severity:** medium

*Evidence:* in this column 398,847 dates can only be day-first and 0 can only be month-first; decision: day_first.

**Handling (standardised).** Read with the column's own convention (day-first), inferred from the dates that can only be read one way; the evidence is recorded in `curated._date_conventions`. This is an inference, not a certainty per value, but the evidence is one-sided and large. `std_dob_basis` = `convention`. A column with both orders present is never guessed.

### F8. CRM phone numbers in 10 different formats

**Affected:** 1,004,777 of 1,020,000 (98.5%) · **Tables:** `customers` · **Severity:** low

**Handling (standardised).** Normalised into `std_phone_primary_e164` (+1 and ten digits). Original kept.

### F9. Card phone numbers masked (`450672xxx81`): digits missing

**Affected:** 384,422 of 660,000 (58.2%) · **Tables:** `card_accounts` · **Severity:** high

**Handling (flagged).** Cannot be repaired. Kept as `std_phone_primary_prefix` and `_suffix` with flag `std_phone_primary_masked`. To be treated as weak evidence in matching (the design rule is that a phone alone never links two records); the identity layer is not built yet.

### F10. CRM postal code written with and without a space, in lower case, or both

**Affected:** 737,153 of 1,020,000 (72.3%) · **Tables:** `customers` · **Severity:** low

**Handling (standardised).** Normalised into `std_postal` (A1A1A1) and `std_fsa`.

### F11. CRM postal code typed with the letter O where the digit 0 belongs (`VOC2S8`, `L6X OZ4`)

**Affected:** 1,887 of 1,020,000 (0.18%) · **Tables:** `customers` · **Severity:** low

*Evidence:* the repair reproduces the CRM column postal_code in 1,887 of 1,887 repaired codes.

**Handling (fixed).** The letter O in a digit position can only be a zero. Repaired into `std_postal` (`std_postal_repaired` = true); `postal_code_raw` is unchanged. An earlier version of this report called these codes truncated and left them empty; that description was wrong.

### F12. Deposit customer key lost its leading zeros (7 or 8 digits instead of the 10-digit CIF)

**Affected:** 156,837 of 780,000 (20.1%) · **Tables:** `deposit_accounts` · **Severity:** high

*Evidence:* after padding to 10 digits, 780,000 of 780,000 deposit rows match a bureau subject (before padding, only the 10-digit ones did).

**Handling (fixed).** New column `cif10` = left-padded key. Use it, not `src_customer_ref`, to join to `external` and `bureau_history`.

### F13. Card first name is only an initial (`J. LANGLOIS`)

**Affected:** 329,786 of 660,000 (50.0%) · **Tables:** `card_accounts` · **Severity:** medium

**Handling (standardised).** Kept as `std_name_first` with `std_name_first_is_initial` = true. Matching uses surname plus initial.

### F14. Deposit first name is only an initial (`DYER D C`)

**Affected:** 778,246 of 780,000 (99.8%) · **Tables:** `deposit_accounts` · **Severity:** medium

**Handling (standardised).** As F13. Deposit names carry no full first name, so deposit name evidence is surname plus initial.

### F15. ALL-CAPS two-word card names with no comma: word order not stated (`JESSICA SMIRNOV`)

**Affected:** 262,897 of 660,000 (39.8%) · **Tables:** `card_accounts` · **Severity:** medium

**Handling (standardised).** Read first-last (cards' convention). Checked against the CRM's own names: about 99% agree.

### F16. ALL-CAPS two-word loan names with no comma: word order not stated (`KAPOOR CASEY`)

**Affected:** 80,607 of 404,500 (19.9%) · **Tables:** `loan_accounts` · **Severity:** medium

**Handling (standardised).** Read last-first (loans' convention). Checked against the CRM's own names: every one agrees.

### F17. Customer salutation spelled several ways (`Mr.`, `mr`, `MR.`; 20 spellings of 7 values)

**Affected:** 128,680 of 1,020,000 (12.6%) · **Tables:** `customers` · **Severity:** low

*Evidence:* found by checking all 192 enum columns for values that differ only by case or spacing; this was the only one.

**Handling (standardised).** New column `salutation_std` with one spelling per value. Original `salutation` is kept. `M.` and `m` are read as the same value.

### F18. Deposit balances in two currencies (7,780 USD accounts among CAD) with no exchange-rate field in the data

**Affected:** 7,780 of 780,000 (1.00%) · **Tables:** `deposit_accounts` · **Severity:** medium

**Handling (flagged).** Amounts are not converted (the data has no rate). Flagged `dq_non_cad`. Totals across accounts must filter to CAD or convert, otherwise they mix currencies.

## 5. Broken references and duplicate customers

Records that point at something that is not there, and people stored more than once.

### R1. Collections case points to a CRM customer that does not exist

**Affected:** 10,365 of 367,229 (2.82%) · **Tables:** `collections_cases` · **Severity:** high

**Handling (flagged).** Rows kept. The missing IDs are listed with row counts in `curated.dq_orphan_customer_refs`. They cannot be tied to a customer record. The identity layer (not yet built) is meant to treat them as unresolved, and customer-level features should leave them out. The IDs have the right format and do not resolve by case, spacing, or duplicate-merge pointers; the likely explanation is CRM records missing from the extract, but the data cannot confirm it.

### R2. Contact record points to a CRM customer that does not exist

**Affected:** 75,500 of 2,688,337 (2.81%) · **Tables:** `contact_history` · **Severity:** high

**Handling (flagged).** As R1.

### R3. Promise to pay points to a CRM customer that does not exist

**Affected:** 6,744 of 238,815 (2.82%) · **Tables:** `promises_to_pay` · **Severity:** high

**Handling (flagged).** As R1. These promises cannot enter a customer-level training set.

### R4. Agent note points to a CRM customer that does not exist

**Affected:** 21,592 of 760,594 (2.84%) · **Tables:** `agent_notes` · **Severity:** medium

**Handling (flagged).** As R1.

### R5. Call transcript points to a CRM customer that does not exist

**Affected:** 695 of 25,000 (2.78%) · **Tables:** `call_transcripts` · **Severity:** medium

**Handling (flagged).** As R1.

### R6. Account snapshot points to a CRM customer that does not exist

**Affected:** 328,891 of 12,569,692 (2.62%) · **Tables:** `account_monthly_snapshot` · **Severity:** high

*Evidence:* 249,817 distinct missing IDs, almost one per row, so these look like stray IDs rather than a few missing customers.

**Handling (flagged).** As R1.

### R7. Contact record points to a collections case that does not exist

**Affected:** 26,864 of 2,688,337 (1.00%) · **Tables:** `contact_history` · **Severity:** medium

**Handling (documented).** Rows kept; not changed. Case-level features must left-join and tolerate a missing case.

### R8. Collections case with no assignment history

**Affected:** 86,725 of 367,229 (23.6%) · **Tables:** `collections_cases` · **Severity:** info

**Handling (documented).** May be legitimate (unassigned or auto-treated cases); not changed. Agent-scope queries will not see these cases.

### R9. Routing rule used in assignments is missing from the reference table (RR-EARLY-03, RR-MID-03, RR-LATE-03, RR-REC-03)

**Affected:** 74,697 of 311,850 (24.0%) · **Tables:** `case_assignment_history` · **Severity:** medium

**Handling (documented).** Not changed. The four codes look like champion/challenger variants of defined rules; the reference table is incomplete. Total column counts non-null rule IDs.

### R10. Same person stored as more than one CRM record (identical national-ID hash)

**Affected:** 36,992 of 1,020,000 (3.63%) · **Tables:** `customers` · **Severity:** high

*Evidence:* 5950 records carry duplicate_of_crm_id, so most duplicate pairs are not marked by the source.

**Handling (deferred to identity layer).** Not merged here. Curation never merges people. The identity layer (not yet built) is meant to link them (national-ID hash is its first exact rule) and assign one golden ID. Affected counts the records sitting in a duplicate group.

### R11. CRM record marked deleted (change-data operation D)

**Affected:** 2,115 of 1,020,000 (0.21%) · **Tables:** `customers` · **Severity:** low

**Handling (excluded from current view).** Kept in `curated.customers`, excluded from `curated.customers_current`.

## 6. Missing values

Values that are blank where they should not be. None were imputed.

### M1. CRM date of birth blank

**Affected:** 5,026 of 1,020,000 (0.49%) · **Tables:** `customers` · **Severity:** low

**Handling (documented).** Left blank. These records cannot use date of birth as evidence.

### M2. Loan date of birth blank

**Affected:** 4,032 of 404,500 (1.00%) · **Tables:** `loan_accounts` · **Severity:** low

**Handling (documented).** As M1.

### M3. Deposit date of birth blank

**Affected:** 15,756 of 780,000 (2.02%) · **Tables:** `deposit_accounts` · **Severity:** low

**Handling (documented).** As M1.

### M4. Bureau date of birth blank

**Affected:** 39,543 of 1,000,000 (3.95%) · **Tables:** `external` · **Severity:** low

*Evidence:* 19,962 of these are no_hit records, where no identity details are expected because the bureau found no file; the other 19,581 are not explained by hit status.

**Handling (documented).** Left blank. The `no_hit` share is expected. The remainder is a real gap and is not imputed; those records cannot use the bureau date of birth as evidence.

### M5. Bureau record says `hit` but has no risk score

**Affected:** 18,937 of 1,000,000 (1.89%) · **Tables:** `external` · **Severity:** medium

**Handling (flagged).** Flagged `dq_hit_without_score`. Treated as a missing score, never as zero.

### M6. Card days-past-due missing

**Affected:** 52,734 of 660,000 (7.99%) · **Tables:** `card_accounts` · **Severity:** medium

**Handling (documented).** Left NULL (not assumed 0). Delinquency features must handle missing.

### M7. Loan days-past-due missing

**Affected:** 32,179 of 404,500 (7.96%) · **Tables:** `loan_accounts` · **Severity:** medium

**Handling (documented).** As M6.

### M8. CRM employer name blank

**Affected:** 359,829 of 1,020,000 (35.3%) · **Tables:** `customers` · **Severity:** info

**Handling (documented).** May be genuine (not employed). Not imputed.

## 7. Values that contradict each other

Fields that cannot all be true at once. Neither value was changed; the rows are flagged.

### L1. Card delinquency bucket disagrees with days past due (e.g. dpd 0 but bucket 1-30, or one band too high)

**Affected:** 24,205 of 660,000 (3.67%) · **Tables:** `card_accounts` · **Severity:** medium

**Handling (flagged).** Neither value is changed (the cause is not known; likely the bucket is fixed at the cycle date while dpd is current). Flagged `dq_bucket_dpd_mismatch`.

### L2. Loan delinquency bucket disagrees with days past due

**Affected:** 14,769 of 404,500 (3.65%) · **Tables:** `loan_accounts` · **Severity:** medium

**Handling (flagged).** As L1.

### L3. Case bucket disagrees with current days past due (one band higher than dpd implies)

**Affected:** 3,807 of 367,229 (1.04%) · **Tables:** `collections_cases` · **Severity:** medium

**Handling (flagged).** As L1.

### L4. Deposit overdraft used exceeds the overdraft limit

**Affected:** 6,343 of 780,000 (0.81%) · **Tables:** `deposit_accounts` · **Severity:** low

**Handling (flagged).** Values kept; flagged `dq_overdraft_over_limit`.

### L5. Right-party contacts exceed total contact attempts on a case

**Affected:** 27,680 of 367,229 (7.54%) · **Tables:** `collections_cases` · **Severity:** low

**Handling (flagged).** May include inbound contacts that are not counted as attempts. Values kept; flagged `dq_rpc_above_attempts`.

### L6. Case promise counts do not add up (kept + broken + open is not total)

**Affected:** 8,747 of 367,229 (2.38%) · **Tables:** `collections_cases` · **Severity:** low

**Handling (flagged).** Values kept; flagged `dq_promise_counts_mismatch`. Promise-level tables are the safer source.

### L7. Promise marked kept but the amount paid is below the promised amount

**Affected:** 2,844 of 238,815 (1.19%) · **Tables:** `promises_to_pay` · **Severity:** medium

**Handling (flagged).** Label noise for any model that predicts broken promises. Values kept; flagged `dq_kept_but_underpaid`. The label rule should decide how to treat them.

### L8. Bureau: oldest trade is younger than newest trade (impossible)

**Affected:** 37,362 of 1,000,000 (3.74%) · **Tables:** `external` · **Severity:** medium

**Handling (flagged).** Not the same rows as the shifted-column incident (K4). Flagged `dq_trade_age_inconsistent`.

### L9. Bureau: file-since date is after the pull date

**Affected:** 17 of 1,000,000 (0.00%) · **Tables:** `external` · **Severity:** low

**Handling (documented).** Not changed.

### L10. Card utilisation outside 0 to 1.5

**Affected:** 33 of 660,000 (0.01%) · **Tables:** `card_accounts` · **Severity:** low

**Handling (documented).** Not changed.

### L11. Hardship offer, or the customer's answer to one, dated after the snapshot date

**Affected:** 1,757 of 40,767 (4.31%) · **Tables:** `offers` · **Severity:** medium

*Evidence:* 688 offers are dated after 2026-09-28 (the latest 2026-10-22), and 1,645 answers (accepted, declined, considering) are dated after it.

**Handling (documented).** Not changed. Treated as not yet known on the snapshot date: the NBA queue counts an offer only if it was made before the as-of date, and its answer and plan status only if the answer came before it (`nba/queue.py` `load_cases`). Otherwise a customer would look as if they had already been offered, or had already broken a plan, when on the as-of date neither had happened.

## 8. Shifts and patterns that affect models

Not wrong values in single rows, but patterns across time or across records that change what a model or a baseline learns. Nothing was changed. None of these is explained by an event in the operations log.

### T1. Collections cases change abruptly from April 2026: new cases surge, cures barely grow, and the 30-day cure rate falls

**Affected:** 147,733 of 367,229 (40.2%) · **Tables:** `collections_cases` · **Severity:** high

*Evidence:* cases opened: 9,430 in Mar 2026, 13,479 in Apr, 33,001 in Aug. Cases paid up per month only move between 8,962 and 12,615 from Apr to Aug. Paid up within 30 days of opening: 60.8% of cases opened Oct 2025 to Mar 2026, 44.0% in Apr, 28.5% in Jul 2026. Cases opened in Apr 2026 that ended charged off, in legal, on insolvency hold or with an agency: 2,361, against 71 a month for Oct 2025 to Mar 2026. Before Apr 2026 the history looks fully settled: 0 of those cases are still open and 98.2% were paid up.

**Handling (documented).** Not changed: the values agree with each other, and no event in the operations log explains the break. Anything trained or measured across Apr 2026 must allow for it: split by time, calibrate on the most recent fully observed months, and do not compare cure or roll rates across the break without saying so. In P3's case model the not-cured rate is 41% in training, 62% in validation and 80% in the holdout, so a model calibrated on earlier months under-predicts; the bank's own `cure_30d` score is badly calibrated after the break too (calibration error 0.25, `reports/model_evaluation.md`). Affected column counts cases opened from Apr 2026.

### T2. Promise break rate rises from May 2026, mostly because fewer promises are for the full overdue amount

**Affected:** 76,727 of 204,369 (37.5%) · **Tables:** `promises_to_pay` · **Severity:** high

*Evidence:* broken or partly kept: 36.4% of promises made Jan 2025 to Apr 2026, then 47.5% in May, 57.7% in Jun and 64.5% in Aug 2026. Promises for the full overdue amount: 49.4% of them before May, 15.2% in Aug. Within each kind the rate moves less: full amount 2.1% to 2.9%, part amount 69.9% to 75.5%. Average days past due when the promise was made: 29.4 before May, 45.8 in Aug.

**Handling (documented).** Not changed. Most of the rise is a change in mix: promises for the full amount are almost never broken, and they become rarer. The rest comes with promises made later in delinquency. It starts a month after T1. Handle it as T1: time-based splits and calibration on recent months. The certified promise-kept rate for periods after Apr 2026 is not comparable with earlier periods without this note. Affected column counts resolved promises made from May 2026.

### T3. Outcomes are recorded late: case cures stop before the snapshot date, and some promises stay open after their grace period

**Affected:** 5,286 of 33,005 (16.0%) · **Tables:** `collections_cases`, `promises_to_pay` · **Severity:** medium

*Evidence:* last case cure recorded on 2026-09-15, while other case statuses run to 2026-09-28 and the snapshot is 2026-09-28; the open promises past their grace period were made in Sep 2026.

**Handling (documented).** Not changed. Use the last fully recorded outcome date (the latest cure date), not the snapshot date, as the end of any outcome window: a case or promise whose window ends after it is unlabelled, not a failure. The P3 training sets already cut their labels there (`features/build_case_training_set.py`, `nba/actions.py`). Affected column counts open promises whose due date plus grace days is before the snapshot date.

### T4. Every promise for 90% to 99.9% of the overdue amount was broken, while promises for the full amount almost never are

**Affected:** 11,277 of 204,369 (5.52%) · **Tables:** `promises_to_pay` · **Severity:** medium

*Evidence:* 11,277 of 11,277 resolved promises for 90% to 99.9% of the overdue amount were broken (typical shortfall $12.47); for exactly the full amount 1.9% were broken, for 50% to 90% 81.6%. The overdue amount on the promise matches the contact that created it for 96.0% of the 90% to 99.9% promises.

**Handling (documented).** Not changed. Nothing in the data explains why a promise a few dollars short of the full amount should always break, so this is most likely a rule of how the synthetic data was made, not customer behaviour. It is not outcome leakage: both amounts are on record when the promise is made (see evidence). Break-risk models learn it, and it carries much of the P3 promise model's lead over the bank's `ptp_break` (`reports/model_evaluation.md`). Do not present it as a finding about customers.

## 9. Is the data uniform now?

Partly, and the difference matters.

**Uniform:**

- Every typed column (numbers, dates, timestamps, booleans) in `curated.*` has one type. Dates that arrived in several formats are recovered where the reading is certain (F1).
- Identity fields (name, date of birth, phone, postal code, employer) are in one format **in the `std_*` columns**, for the five identity sources.
- Deposit keys are 10 digits in `cif10`; contact channel is in one column, `channel_resolved`; salutation is one spelling in `salutation_std`.
- No exact duplicate rows; primary keys are unique in the four identity tables.

**Not uniform, on purpose or not yet:**

- **The original columns are untouched.** `dob_raw`, `*_phone_raw`, `*_name_raw`, `src_customer_ref` and the rest still carry each system's own format. Use the `std_*` and derived columns, not the originals.
- **Customer keys still differ by system** (CRM ID, card number, loan number, 10-digit CIF). Joining them into one customer is the identity layer's job; curation only repairs the CIF format.
- **The problems flagged above stay in the data.** Flagged rows are marked, not repaired (masked phones, shifted bureau columns, bucket and days-past-due disagreements, missing CRM customers).
- **Currency is mixed** in deposit balances (flag `dq_non_cad`); no conversion is possible without a rate.
- **Not examined:** free-text content (notes, transcripts), timezone differences between local and UTC timestamps, and enum values against reference lists beyond the contact codes in C20 and the case and spelling check that found F17.

## 10. Method and limits

1. The six operations-log events were each searched for in the data, and the signature in the incident reports (a count, a drop in calls, a rate against baseline) was checked.
2. For unlisted issues the data was profiled: formats per field per system, references between tables, missing values, contradictions between fields, reference codes, and case or spacing variants in all 192 enum columns.
3. A rule finds the records it is written to find. Where the operations log gives a count and a rule finds fewer (K4), the rule is the lower bound, not the true number. Where the log cannot be matched to rows (K3), this report says so.
4. **Not covered yet:** unmatched records and open review items. They come from the customer identity step, which is not built; this report will gain them when it is.
5. Unlisted issues may remain. The checks cover what was thought of; new data re-runs them automatically but does not add new kinds of check.

## 11. Checks that found nothing

| ID | Check | Rows checked |
|---|---|---:|
| C23 | Postal code still invalid after cleaning (customers, cards, loans, deposits) | 2,864,500 |
| C21 | Date of birth still ambiguous after resolution (customers, cards, loans, deposits, bureau) | 3,864,500 |
| C22 | CRM date_of_birth disagrees with the only possible reading of dob_raw | 213,884 |
| C1 | Exact duplicate rows removed (all tables) | 68,162,322 |
| C2 | Customer date of birth in the future, or age under 18 or over 110 | 1,020,000 |
| C3 | Customer stored age differs from age computed from date of birth by more than 1 year | 1,020,000 |
| C4 | Customer relationship starts before date of birth | 1,020,000 |
| C5 | Customer tenure disagrees with relationship start by more than 2 months | 1,020,000 |
| C6 | Cease-contact, deceased or bankruptcy flag set with no matching date | 1,020,000 |
| C7 | Declared income zero, negative or over 5,000,000 | 1,020,000 |
| C8 | CRM record created after its extract time | 1,020,000 |
| C9 | Duplicate primary keys in the four identity tables | 1,020,000 |
| C10 | Card account opened after its snapshot date | 660,000 |
| C11 | Loan matures before it was originated | 404,500 |
| C12 | Loan balance above original amount, or interest rate outside 0 to 40 | 404,500 |
| C13 | Deposit account opened after its snapshot date | 780,000 |
| C14 | Case status dated before the case opened | 367,229 |
| C15 | Promise due date before it was created, non-positive amount, or paid before created | 238,815 |
| C16 | Call ends before it starts, duration off by more than 5 s, or talk ratios above 1.05 | 25,000 |
| C17 | Contact dated after the as-of date | 2,688,337 |
| C18 | Bureau pull dated after the as-of date, or negative balances and counts | 1,000,000 |
| C19 | Bureau score outside 300 to 900 | 1,000,000 |
| C20 | Contact reference codes (strategy, outcome, templates, scripts) not in the reference table | 2,688,337 |

## 12. Reproduce

```bash
python -m trustissues.factory.quality.dq_report     # applies the flags and derived columns, rewrites this file
```

Handling artefacts created by the run: derived columns `channel_resolved`, `cif10`, `salutation_std`; flag columns `dq_*` on the affected tables; the table `curated.dq_orphan_customer_refs`.
