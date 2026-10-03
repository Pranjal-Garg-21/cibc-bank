# P3 plan: what changes when the golden records land

**Owner:** P3 · **Written:** 3 Oct, evening · **Branch:** `p3/features-model`

Everything P3 built so far runs on `curated.*`, keyed by `crm_customer_id` and `account_id`, through one module
(`features/sources.py`). This plan covers what P3 does once P1 publishes identity resolution and the gold layer
(`gold.customer_xref`, `gold.promises_enriched`, `gold.decisioning_customer`, `gold.customer_safeguards`,
`gold.c360_*`) and P4 publishes `gold.text_features`. It lists the checks to run first, the switch-over in order, the
interface gaps to close with P1 and P4, the new features to try, and how each step is judged. Steps are ordered so the
system keeps working after each one: every step ends with the tests passing and the morning run (`make nightly`)
producing a queue.

---

## 1. What P3 is waiting for

| Gold object (owner) | What P3 uses it for | Today's stand-in |
|---|---|---|
| `gold.customer_xref` (P1) | One customer across CRM duplicates, cards, loans, deposits, bureau | `crm_customer_id`; deposits and bureau unused |
| `gold.promises_enriched` (P1) | The promise spine and label (`label_broken`) | `curated.promises_to_pay` |
| `gold.decisioning_customer` (P1) | Allow-listed customer attributes, history grain (`available_from` as the point-in-time key) | Not used |
| `gold.customer_safeguards` (P1) | Rules and hardship routing (consent, cease, deceased, bankruptcy, dispute, hardship, vulnerability) | `curated.customers` via `crm_safety_flags` and case flags |
| `gold.c360_account` (P1) | Accounts per golden customer | `account_monthly_snapshot.crm_customer_id` |
| `gold.text_features` (P4) | Hardship signal, stated delay reason, promise intent, dispute mention, with evidence | Not used; cards say "no text signals yet" |

## 2. First hour after gold lands: checks before switching anything

Run these as queries, record the results in `docs/p3_plan.md`, and do not switch a source that fails its check.

1. **Coverage.** Share of promises, open cases, contacts and account snapshots whose customer gets a
   `golden_customer_id` with status `linked` or `singleton`. Today 2,568 open cases (and 6,744 promises, 75,500
   contacts) point at a CRM customer that does not exist (DQ checks R1–R3): how many does identity resolve?
2. **Merges.** Number of CRM records per golden customer (the DQ report found 36,992 sharing a national-ID hash).
   Spot-check 20 merged clusters for conflicts (two dates of birth, two national IDs).
3. **Deposits and bureau.** Share of deposit accounts (`cif10`) and bureau subjects linked to a golden customer, and
   whether bureau `bank_subject_ref` equals the deposit CIF (implementation plan §11).
4. **Label.** `gold.promises_enriched.label_broken` must equal P3's label (broken or partially kept = 1, kept = 0;
   open and cancelled excluded) on every resolved promise. Any difference is a definition question for P1.
5. **Leakage split.** `PROMISE_OUTCOME_COLUMNS` in `common/interfaces.py` matches the outcome block of
   `promises_enriched` (an existing test checks this; run it).
6. **Point-in-time grain.** `decisioning_customer` has one row per `(golden_customer_id, valid_from)` with
   `available_from >= valid_from`. Check how many customers have more than one row, and that `available_from` is never
   after the snapshot date.
7. **Sensitivity.** Regenerate `config/sensitivity.yaml` (`python -m trustissues.factory.ingest.dictionary`) so the gold
   columns are labelled; every column P3 will read must be `none` (the source tests enforce this).

## 3. Interface gaps to close (agree before switching)

These are columns P3 uses today that the agreed gold shapes (`contracts/interfaces/stubs.sql`) do not have. Ask P1 to
add them, or keep reading them from curated through the crosswalk until they are added.

| Gold object | Missing column(s) | Used for today |
|---|---|---|
| `customer_safeguards` | `consent_push` | Push reminders (consent is default deny) |
| `customer_safeguards` | `internal_do_not_call` | The do-not-call rule (internal list only, POL-COLL-001 §5.3) |
| `customer_safeguards` | `represented` (legal representative, credit counsellor, third party) | Hard stop: contact through the representative |
| `customer_safeguards` | `insolvency` covering consumer proposals as well as bankruptcy | Hard stop (POL-COLL-001 §7.3) |
| `customer_safeguards` | `sunday_contact_permitted`, `permitted_call_start_local`, `permitted_call_end_local`, `preferred_contact_window_start/end`, `time_zone` | The day's contact window |
| `customer_safeguards` | consent as BOOLEAN, or an agreed value list for `consent_*` VARCHAR | Rules read booleans |
| `promises_enriched` | `reminder_scheduled_flag` | Feature `reminder_scheduled_flag` (promise model) |
| `promises_enriched` | `crm_customer_id` alongside `golden_customer_id` | Fairness joins and the transition period |
| `text_features` (P4) | Confirm `ptp_intent_strength` values (`strong`/`moderate`/`weak`/`none`) and `confidence` range | Features and the hardship level |

Also confirm with P1: case-level flags (`collections_cases.hardship_flag`, `hardship_source`, plan fields,
`vulnerable_customer_flag`, `test_cell`, `contacts_last_7d`) stay on the case, since they belong to the case, not the
customer. The rules keep reading them from the case and combine them with the customer's safeguards.

## 4. Switch-over, in order

Each step: what changes, files, how it is checked, rough effort.

### S1. Sources point at gold (1–2 h)
- `features/sources.py`: `promises` → `gold.promises_enriched` (creation-time columns plus `ptp_status` / `status_ts` as
  now, masked until known); add `customer_xref` and `c360_account` sources; `crm_safety_flags` →
  `gold.customer_safeguards` (plus curated for the gap columns in §3 until they are added).
- Keep the current column names as output names, so the feature SQL does not change in this step.
- **Check:** the source tests pass; rebuilding the spines and features on gold gives the same row counts as on
  curated for promises that have a golden ID; the leakage tests pass.

### S2. The customer becomes the golden customer (2–3 h)
- Spines (`build_training_set.py`, `build_case_training_set.py`) and the queue carry `golden_customer_id`.
- Customer-level features join on it: `promise_history.sql` (earlier promises of the same person across CRM duplicates),
  `channel_response.sql`, and the customer fallbacks in `delinquency.sql`, `liquidity.sql`, `payday.sql` (accounts via
  `c360_account` instead of `account_monthly_snapshot.crm_customer_id`).
- Feast: the customer entity becomes `golden_customer_id` (`feature_repo/entities.py`); `feast apply`.
- `gold.nba_decisions.golden_customer_id` holds the real golden ID (today it holds `crm_customer_id`).
- **Check:** retrain both models with no new features; holdout AUC and calibration must not get worse than today
  (case 0.838, promise 0.950) beyond noise. More history per person should, if anything, help `promise_history`.

### S3. Rules and contact at the customer level (2 h)
- Consent, cease contact, deceased, insolvency, do-not-call and the contact window from `customer_safeguards`, the
  most restrictive value across a customer's records (P1 builds it that way).
- **New:** one person with several open cases (or several CRM records) must not get several contacts on the same day.
  After allocation, keep the most valuable contact per golden customer per day and turn the others into `wait`
  (`no_capacity`-style reason `customer_already_contacted_today`); count the 7-day cap per customer, not per case.
- The 2,568 open cases that point at a missing CRM customer get consent and safeguards once identity resolves them.
- **Check:** new tests (two cases of one customer get one contact; most restrictive consent wins); measure on the
  28 Sep queue how many same-day duplicate contacts this removes and how many orphan cases become contactable.

### S4. Hardship across the customer (1–2 h)
- Hardship or vulnerability on any of a customer's records or cases routes all their cases to the same certified owner
  (`allocate.assign_owners` keyed by golden customer).
- Programme eligibility per customer: `uses_12m` and broken plans counted across the customer's accounts; months on book
  from the customer's oldest relevant account.
- P4's text features: hardship level `clear` when a note or call states hardship (`hardship_signal` with confidence above
  an agreed threshold), which opens the programmes that need it (fixed payment plan, reduced rate, debt management
  plan, term extension); the evidence sentence goes on the card (`DecisionCard.evidence`, already in the shape).
- **Check:** the hardship tests stay green (never harsher, no reminders); report how many cases change support reason.

### S5. New features: try each, keep only what helps (3–5 h)
Same protocol as the 3 Oct experiments (`docs/p3_plan.md`, "Promise model experiments"): two time folds (before the April
shift and the official holdout), AUC overall and among partial-amount promises, AUC without the amount features, and
calibration by month. A feature group is adopted only if it helps on both folds, then passes the proxy audit (S7).

| Group | New SQL | Source | Notes |
|---|---|---|---|
| Payday | `payday.sql` v2: days from the promise due date to the next expected salary credit, salary lateness, missed salary | `salary_credit_history` via deposit → golden | Replaces the interim snapshot salary features; the stated income date always equals the due date, so it carries nothing |
| Liquidity | `liquidity.sql` v2: deposit balance vs promise amount, overdraft use, returned items in 90 days | `deposit_accounts`, `transactions` | Never use `transactions.ptp_id_matched` (it ties a payment to its promise: the outcome) |
| Bureau | `bureau.sql`: score change over 90 days normalised by `risk_score_model` range, inquiries, new delinquencies | `bureau_history` / `external` via golden | Exclude rows flagged `dq_inquiries_inconsistent` (DQ check K4) and `dq_hit_without_score` (M5) |
| Customer | from `decisioning_customer` as of `available_from`: tenure, product count, pay frequency, income verified | `gold.decisioning_customer` | Screen each for proxies first (province, housing, occupation can stand in for protected attributes) |
| Text | hardship signal, stated delay reason, promise intent, dispute mention (latest before the event, with confidence) | `gold.text_features` | Only after P4's `text_eval` gate passes; covers promises in the training window and holdout, not just open cases |
| Repayment | the card statement / loan instalment groups tried on 3 Oct, now at customer level across products | `card_statements`, `loan_instalments` | Did not help at account level; retry only once linked across products |

Expectation, from the 3 Oct experiments: the promise outcome in this data is driven mostly by the promised amount, so
gains on the promise model may be small; the case model and the hardship routing are where linked data should matter
most.

### S6. Retrain, re-evaluate, refresh everything downstream (1–2 h)
- `make train` (spines, features, incumbent baseline, both models, action values, Feast), then `make nightly`.
- Recalibrate the case model on the most recent fully observed month if the April-shift gap persists (monitoring shows
  it at "watch": −0.084 in July).
- Update `reports/model_evaluation.md` (regenerated), `docs/p3_plan.md` status tables, and the numbers in
  `docs/p3_submission_text.md`.

### S7. Governance gates before the new queue is used (1 h)
- **Fairness** (`nba/fairness.py`): protected attributes joined via the golden customer (from `customers` through
  `customer_xref`, still only in `fairness.py`). The "not in CRM" group should shrink to the cases identity could not
  resolve. No new calibration flag may appear for ours; re-check the small language groups flagged on 3 Oct.
- **Proxy audit:** re-run with the new features. Any feature group that raises a protected attribute's proxy AUC
  above 0.60 is reviewed, and dropped if the outcome check shows the model treating that group differently.
- **Monitoring** (`governance/monitoring.py`): reset the drift references to the new holdout (feature distributions
  change by construction when customers merge), then check the first nightly run.
- **Leakage:** the point-in-time tests cover the new SQL files (add each new group to `compute.GROUPS` /
  `CASE_GROUPS`, which puts it under `test_feature_leakage.py`), and `decisioning_customer` joins on `available_from`.

### S8. App and cards (1 h)
- Decision card: the golden customer, all of that customer's open cases, the evidence quotes from text features, and
  the customer's hardship history across cases.
- Agent queue: one row per customer per day when they have several cases (follows S3).

## 5. Tests to add
- Source tests against the gold columns (default-deny on sensitivity labels; the fairness source still the only
  exception).
- `decisioning_customer` as-of join: a value that became available after the event time is never used.
- Customer-level contact: one contact per golden customer per day; the 7-day cap per customer.
- Most restrictive consent across a customer's records.
- Text features: a note dated after the event time is never used; evidence on the card matches the signal.
- The label from `promises_enriched` equals P3's label on resolved promises.

## 6. Order and effort

| Step | Needs | Effort | After it |
|---|---|---|---|
| §2 checks | gold published | 1 h | Decide which sources to switch |
| §3 gaps | P1 / P4 agreement | 0.5 h to agree, P1's time to add | Rules can read gold |
| S1 sources | §2, §3 | 1–2 h | Same results on gold |
| S2 golden customer | `customer_xref`, `c360_account` | 2–3 h | Customer-level features, real golden IDs |
| S3 rules and contact | `customer_safeguards` | 2 h | One contact per person per day |
| S4 hardship | S2, S3; P4 for text | 1–2 h | Hardship across a person's cases; programmes needing `clear` |
| S5 new features | deposits, bureau, text linked | 3–5 h | Only groups that help on both folds |
| S6 retrain | S1–S5 | 1–2 h | New models and queue |
| S7 gates | S6 | 1 h | Fairness, proxy audit, monitoring, leakage clean |
| S8 app | S2–S4 | 1 h | Cards show the whole customer |

If time is short, the minimum useful path is §2 → S1 → S2 → S3 → S6 → S7: real golden IDs, one contact per person per
day, the orphan cases resolved, and the governance checks re-run. New feature groups (S5) are the first to drop.
