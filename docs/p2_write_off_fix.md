# BQ-015 write-off correction — 3 October 2026

Claude identified that the exact snapshot-date filter discarded every account with a charge-off date. Those lifecycle rows retain older dates. `write_off_rate.sql` now uses `snapshot_date <= $snapshot_date`, retaining those accounts while excluding observations after the as-of cutoff. The numerator remains `chargeoff_date >= start AND chargeoff_date < end`.

The rate denominator includes every retained card/loan account observed by the cutoff, without an active-status restriction. Closed accounts and charge-offs outside the requested event window still belong to that population. The metric catalogue specifies no active-account filter for write-off. Deposit accounts are absent from this semantic view.

Read-only validation on `warehouse/ask_dev/trustissues_ask_dev.duckdb`, through the team-lead validator/executor, reproduced all six requested results for `[2025-09-29, 2026-09-29)` with as-of 28 September 2026:

| Product | Charge-off accounts | Amount (CAD) | Rate denominator |
|---|---:|---:|---:|
| auto_loan | 137 | 3,718,099.20 | 94,500 |
| card | 360 | 2,007,836.32 | 660,000 |
| mortgage | 4 | 1,235,398.37 | 70,000 |
| personal_loan | 236 | 3,207,027.83 | 140,000 |
| student_loc | 2 | 25,929.27 | 1,926 |
| unsecured_loc | 102 | 947,148.47 | 98,074 |

The population is 1,064,500 rows and 1,064,500 distinct account IDs, all observed by the cutoff; 861,596 rows have the exact as-of snapshot date. There are 3,799 accounts with any charge-off date, including dates outside the requested window. The new denominator is the full 1,064,500, grouped by product above. This works for the inspected one-row-per-account release view; adding repeated account history would require a reviewed latest-row rule to avoid double counting.

`delinquency_rate.sql` remains unchanged. BQ-012 asks for current active card accounts, and its released gold SQL explicitly requires exact snapshot date, status `active`/`blocked`, and non-NULL DPD. The read-only dev run matched all seven gold product rates (rounded to two decimals) and their population counts. Closed, charged-off and unknown-status accounts are excluded independently of date. Loan active-status inference remains unsupported.

Regression tests cover an older charged-off snapshot, an older closed account in the denominator, inclusive charge-off start, exclusive end, future snapshots excluded, and unchanged current delinquency eligibility. Validation:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B -m pytest \
  tests/test_metric_templates.py -q -p no:cacheprovider
```

Result: **22 passed in 4.87 seconds**. No worker warehouse was opened or changed. BQ-014, production publication and contract binding remain pending on P1; Claude's in-progress integration files were left untouched.
