# Consumer views, text controls and the publish gate

**Status: built and published on the real data (2026-10-03).** Everything P1 builds lands in `gold_stage`; `publish.py` runs the data contract and copies it to `gold` only if every blocking rule passes. Consumers read `gold` only. Identity and the C360 tables are in [`identity.md`](identity.md).

```bash
python -m trustissues.factory.curate.text_controls       # eligible, masked notes and transcripts (seconds)
python -m trustissues.factory.gold.views                  # the five views into gold_stage (~1.5 min)
python -m trustissues.factory.quality.contract_checks     # run the contract on gold_stage, print results
python -m trustissues.factory.gold.publish                # gate + copy gold_stage -> gold (~20 s)
python -m trustissues.factory.pipeline --from identity    # identity, C360, views, publish, report
```

## 1. Defaults that need the team's confirmation

These were open in [`interfaces.md`](interfaces.md) or the plan. Each is a working default, chosen to be safe, and easy to change in one place.

| # | Question | Default | Why | Who confirms | Where to change |
|---|---|---|---|---|---|
| A | Is `partially_kept` a broken promise? (open question 6) | **No.** `label_broken` = true for `broken`; false for `kept` and `partially_kept`; NULL for `open` and `cancelled` | Matches the certified `broken_promise_rate` (broken ÷ kept + partially kept + broken). P3 can still build its own label from `ptp_status` | P3 | `views.promises_enriched` |
| B | `decisioning_customer` grain (open "history grain") | **A history table.** A row starts whenever a source changes for the person: CRM version, quarter-end account snapshot, bureau pull, and today. Each row holds the latest values available at its `available_from`. Train with an as-of join on `available_from <= ptp_created_ts` | No future values can leak into training | P3 | `views.decisioning_customer` |
| C | When does data become known? | Snapshots and bureau pulls: next day 06:00 (nightly batch); bureau files use `file_received_ts` when present. CRM: the version's `effective_from_ts` | Matches the batch schedule in the design | P3 | same |
| D | CRM attributes before the CRM version started | **NULL**, not back-filled. CRM keeps one version per record, so values from before its start date are unknown | Back-filling would leak today's income or employment into old promises. Cost: CRM attributes are available for 0% of pre-2022 promises, 46% in 2024, 71% in 2025, 95% in 2026 (54% of all promises are from 2026). Account and bureau history cover about 95% from 2024 | P3 | same |
| E | `total_overdue` in history | Only on the current row; NULL earlier | The quarter-end snapshot has no overdue amount | P3 | same |
| F | `safety_flag` in `customer_safeguards` | Any hard stop: cease contact, deceased, bankruptcy, open dispute | These are the four the rules engine must never miss. Third-party representation, power of attorney, insolvency, DNCL etc. are separate extra columns | P3 | `views.customer_safeguards` |
| G | Which notes may be used? (plan §11 open item) | Excluded: `needs_review` and past `retention_until`. **`redaction_status = none` is treated as "no redaction needed" and used** | The P1 design excludes only `needs_review` and expired notes. 95% of notes are `none`; excluding them would leave the timeline almost empty | P4, compliance | `curate/text_controls.py` |
| H | Can the upstream redaction be trusted? | **No: we mask every eligible note and transcript ourselves** (card numbers, SINs, phones, emails, a date after "DOB"). `masked` marks what changed | Card numbers were found in 1,881 `none` notes and in 108 `auto_redacted` notes. After masking, no card-number pattern is left | P4 | `text_controls.MASKS` |
| I | Who owns a collections case when its fields disagree? | **The holder of the accounts it lists.** `gold.case_customer` keeps both readings and sets `case_customer_mismatch` (1,265 cases). Promises, contacts, notes, transcripts and offers follow the case's resolved owner | The case's CRM ID is one character off the holder's in every mismatch, and the case's province matches the holder 433 times out of 436. It also ties 11,125 of the 11,137 cases whose CRM ID does not exist to a person through their accounts | P2, P3, P4 | `build_c360.build_case_customer` |
| J | `bucket_transitions` grain (open question 7) | Consecutive quarter-end snapshots of the same account, with `status_start/end`, `rolled_forward`, `rolled_back` | The snapshot table is quarterly. The roll-rate metric's filter (exclude charged-off and insolvency hold) is left to the query, using `status_start` | P2 | `views.bucket_transitions` |
| K | Consent column names (open question 3) | `consent_phone` = CRM `consent_call`, `consent_mail` = CRM `consent_letter`; `consent_push` and `consent_autodialer` added as extras | Keeps the agreed stub names | All | `build_c360` |
| L | Data quality tool | Plain SQL rules in the contract YAML, not Great Expectations | The rules are SQL already; one fewer dependency | P2 | `quality/contract_checks.py` |

`semantic_*` views are **not built**: P2 has not said which metric shapes it needs (open question 1).

## 2. The views

| Table | Rows | For | Notes |
|---|---|---|---|
| `gold.promises_enriched` | 238,815 | P3, P2 | Stub columns in agreed order (creation-time, then outcome). Extras: `crm_customer_id`, `owner_resolved_by`, `case_customer_mismatch`, `dq_kept_but_underpaid`. 19 promises have no owner |
| `gold.customer_safeguards` | 1,013,152 | P3 rules engine | Never a model input. 67,763 vulnerable; 50,990 with a hard stop |
| `gold.decisioning_customer` | 14,860,128 | P3 feature store | Allow-list of 21 columns. The build fails if the columns drift; a test and contract rule Q4 check them against `config/sensitivity.yaml` |
| `gold.customer_timeline` | 4,808,959 | P4 RAG, agent screen | Contacts 2.69M, notes 710k, promises 239k, payments 1.11M, offers 40k, transcripts 24,914. Events up to AS_OF_DATE only. `text` holds only eligible, masked text; `summary` describes the event |
| `gold.bucket_transitions` | 10,933,834 | P2 roll rate | One row per account per quarter-to-quarter step |
| `gold.case_customer` | 367,229 | everyone joining cases | See default I |

## 3. The publish gate

The contract is [`contracts/c360_customer.yaml`](../contracts/c360_customer.yaml): owner, grain, refresh, freshness, classification, schema, allowed and prohibited uses, and the rules. The rules are SQL in the same file, so the contract and the checks cannot drift apart. Result of the first run:

| Rule | Blocking | Result |
|---|---|---|
| Q0 every P1 table has the agreed interface shape | yes | pass |
| Q1 golden ID unique, not null | yes | pass |
| Q2 every source record in `customer_xref` exactly once | yes | pass (3,487,885) |
| Q3 no link below 0.80; 0.80–0.95 only via LLM or a person | yes | pass |
| Q4 no protected, PII or proxy column in `decisioning_customer` | yes | pass |
| Q5 a safety flag true in any source is true in gold | yes | pass (70,112 source flags) |
| Q6 DOB valid, age 18–110 | warn | 9,027: all are people with no DOB on file; none has an impossible age |
| Q7 balances ≥ 0, totals = sum of accounts | warn | 18: cards with a credit balance (the customer overpaid) |
| Q8 bureau score within model range | warn | pass. Both bureau tables score 300–850 (the design assumed 300–900 for one) |
| Q9 source no more than 24 h behind AS_OF_DATE | warn | pass |

If a blocking rule fails, or cannot run, nothing is copied and `gold` keeps the last good publish. Every attempt is logged in `gold._publish_log`; rule results are in `gold._contract_results`.
