# Values that cannot be read with certainty: the policy

**Status: implemented for dates of birth and typed date columns; one safeguard is proposed but not built (section 7).** Written 2026-10-03 after the first full-data run. This is the reasoning and the evidence behind how curation treats a value it cannot be sure of. The numbers are from the real data and can be re-queried (section 8).

## 1. The principle

1. **Never silently invent a value.** A guess that is wrong looks exactly like a fact.
2. **Never silently lose a value.** A value turned into NULL must still be recoverable and countable.
3. **Always record how a value was decided**, so anyone can tell a certain value from an inferred one.

When a value could mean more than one thing, curation uses the strongest available evidence and falls back only when it is missing.

## 2. The ladder, strongest evidence first

Applied in this order to every date of birth. The first rung that gives an answer wins, and the column `std_dob_basis` records which one it was.

| Rung | Evidence | Certainty | `std_dob_basis` | Where it is used in this data |
|---|---|---|---|---|
| 1 | **Only one reading is possible.** A part above 12 cannot be a month (`13-01-1990` can only be day-first); year-first formats (`1990-01-13`, `19900113`) and month names (`Jan 13 1990`) have one reading | Certain | `exact` | All sources |
| 2 | **The source's own cleaned column**, if one exists and has been checked against rung 1 | Certain, once validated | `source_clean_column` | CRM only (`customers.date_of_birth`) |
| 3 | **The column's own convention**, inferred from the values in that column that can only be read one way | High, not certain per value | `convention` | Cards only (`DD-MM-YYYY`) |
| 4 | **Another independent source of the same fact** (for example the CRM date of birth of the same person, once identity matching links them) | High | not built | Planned as validation, see section 6 |
| 5 | **Keep both readings and flag** (`std_dob` primary, `std_dob_alt` the other, `std_dob_ambiguous` = true). Matching tests both | Honest | `ambiguous` | Nothing in this data after rungs 2 and 3, but the rule stays for new data |
| 6 | **No valid reading at all** (`31/02/1990`, `not a date`): the typed value is NULL and the original text is kept | Honest | none | Nothing in this data |

## 3. Evidence from the real data

### CRM (`customers.dob_raw`, slash format, 354,791 rows)

The text column is **mixed**: both orders occur, in the same proportions in every record source, so no column-wide rule is safe.

| Dates that can only be... | Rows |
|---|---:|
| day-first (first part above 12) | 122,353 |
| month-first (second part above 12) | 91,531 |
| ambiguous (both parts 12 or less, different) | 128,980 |
| day and month equal (not ambiguous) | 11,927 |

By record source the split is the same everywhere (day-first : month-first is about 57 : 43 in `branch_onboarding`, `online`, `migration_2019` and `call_centre`), so the mix is not explained by the channel.

The CRM also carries its own cleaned `date_of_birth`. It was tested against everything that can be checked:

| Test | Result |
|---|---|
| Agrees with the only possible reading of the day-first dates | 122,353 of 122,353 |
| Agrees with the only possible reading of the month-first dates | 91,531 of 91,531 |
| Agrees with the ISO dates | 609,542 of 609,542 |
| For the 128,980 ambiguous dates: equals the month-first reading / the day-first reading / neither | 55,204 / 73,776 / **0** |
| Slash-format rows with no cleaned date | 0 |

Zero disagreements in about 823,000 checks, so the cleaned column is trusted and **settles every ambiguous CRM date with certainty** (rung 2). This column existed from the start and was overlooked in the first build, which kept both readings for these rows.

### Cards (`card_accounts.cardholder_dob_raw`, `DD-MM-YYYY`, 660,000 rows)

| Dates that can only be... | Rows |
|---|---:|
| day-first | 398,847 |
| month-first | **0** |
| ambiguous | 239,324 |
| day and month equal | 21,829 |

There is no cleaned column, but the evidence is one-sided and large: 398,847 day-first against 0 month-first. The convention is day-first (rung 3), and the 239,324 ambiguous values are read that way.

### The account snapshot (`account_monthly_snapshot.month_end_date`)

| Shape | Rows |
|---|---:|
| `DD/MM/YYYY` (first part above 12) | 418,752 |
| `MM/DD/YYYY` (second part above 12) | 418,319 |
| `YYYYMMDD` | 418,081 |

Again mixed, so no column rule is safe. Here it does not matter: every value is a month-end, so the day is always above 12 and every value has exactly one reading (rung 1). All 1,255,152 non-ISO values are recovered.

## 4. Why not "one instance is enough"

One value with a part above 12 proves which part is the day **for that value**. It does not prove the column is consistent. In the CRM a single day-first example and a single month-first example both exist; whichever was seen first, a rule built from it would be wrong for a large share of the ambiguous values and nothing would warn about it. Measured against the CRM's cleaned column: assuming day-first is wrong for 42.8% of the 128,980 ambiguous dates (55,204), and assuming month-first is wrong for 57.2% (73,776).

The real question is "does any value point the other way?" Only a count can answer it. For cards the count is 0 out of 398,847. For the CRM it is 91,531. The count is one query per date column and takes seconds.

## 5. How the convention is inferred (rung 3)

`standardise.infer_date_convention(first_over_12, second_over_12)` returns a convention only when **both** hold:

- at least **1,000** values can only be read one way (enough evidence), and
- the minority order is at most **0.1%** of them (one-sided).

Otherwise it returns nothing and the column is treated as mixed. The decision and the counts are written to `curated._date_conventions` (table, column, `first_over_12`, `second_over_12`, `ambiguous_rows`, `decision`). A convention never overrides a value that has only one reading.

The two numbers are the only dials (`min_evidence`, `max_minority_share`). Looser thresholds accept noisier columns; stricter ones leave more values ambiguous.

## 6. The typed-column rule: when a value becomes NULL

For any column typed as a date, number or boolean, a value that does not convert is handled like this:

1. Tried as ISO first. If that fails, for **date** columns, `YYYYMMDD` and `DD/MM/YYYY` or `MM/DD/YYYY` are tried, accepted only if exactly one reading is valid (rung 1 logic).
2. If still no valid reading, the typed value is **NULL**, the original text is kept in `<col>__raw` on that row, and the failure is counted in `curated._cast_failures`.
3. Values recovered by step 1 are counted in `curated._format_recoveries`, so a recovery is as visible as a failure.

In this data after the fixes: **0 values are NULLed** by this rule. It exists for data that arrives later.

**The remaining weakness.** A NULL looks like "the source had nothing", and downstream code that does not check will drop those rows. That is how the 10% of snapshot rows with no `month_end_date` could have gone unnoticed in the first build. The failure table caught it, but only because it was read.

**Validation still to do (rung 4).** Once identity matching links a card holder to a CRM person, compare the card date of birth (read day-first) with the CRM `date_of_birth`. Agreement near 100% confirms rung 3 on real linked people; a clear gap would mean the convention inference needs revisiting. Not possible yet, because identity matching is not built.

**Limitation.** The typed-column fallback (step 1) does not use column conventions; only the identity date-of-birth columns do. A typed date column that is one-sided but ambiguous would stay NULL. None exists in this data.

## 7. Proposed, not built: fail loudly

The most reliable change still open is to make the build **stop** when too many values in a column fail to convert (for example more than 0.1% of rows) instead of continuing and relying on someone to read the failure table. The 10% case would have been caught on the first run. Small amounts of mess would still pass, with the failures listed. This changes build behaviour, so it has not been added without a decision.

## 8. How to check it yourself

```sql
-- the evidence and the decision for each date column
select * from curated._date_conventions;

-- how each identity date was decided, per table
select std_dob_basis, count(*) from curated.customers     group by 1;
select std_dob_basis, count(*) from curated.card_accounts group by 1;

-- anything still ambiguous (should be 0 in every identity table)
select count(*) from curated.card_accounts where std_dob_ambiguous;

-- every value that became NULL or was recovered
select * from curated._cast_failures;
select * from curated._format_recoveries;
```

The same facts are checked on every run of the data quality report (`reports/data_quality_report.md`, items F6, F7, C21 and C22).
