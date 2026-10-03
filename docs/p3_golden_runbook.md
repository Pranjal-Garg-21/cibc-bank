# P3 runbook: when the golden records land

Step-by-step checklist for switching P3 from `curated.*` + `crm_customer_id` to the gold layer + `golden_customer_id`.
The reasoning behind each step is in [`p3_golden_plan.md`](p3_golden_plan.md). Work top to bottom; tick each box; do
not start a step until the one before ends with **tests green and `make nightly` producing a queue**. After every step,
write what changed and the measured numbers into [`p3_plan.md`](p3_plan.md) and commit.

**Before you start**
- [x] Pull `main` (P1's gold build, P4's text features) into `p3/features-model`; resolve conflicts; `python -m pytest tests -q`.
- [x] Stop the app (`make app` holds the warehouse write lock).
- [x] Rebuild your warehouse with P1's pipeline (`make pipeline`, plus P1's identity / gold stages) and confirm with
      `python scripts/verify_warehouse.py`.
- [x] Note the current numbers to compare against: case AUC **0.838** (bank 0.725), promise AUC **0.950** (bank 0.695),
      86,059 open cases, 2,568 open cases pointing at a missing CRM customer, 12,359 support calls.

Open a read-only shell for the checks: `.venv/bin/python -c "import duckdb; con = duckdb.connect('warehouse/trustissues.duckdb', read_only=True); ..."`
or `duckdb -readonly warehouse/trustissues.duckdb`.

---

## Step 0. Checks (about 1 h). Do not switch anything that fails.

- [x] **Gold objects exist and are not empty**
  ```sql
  SELECT table_name, estimated_size FROM duckdb_tables() WHERE schema_name = 'gold' ORDER BY 1;
  ```
  Need: `customer_xref`, `promises_enriched`, `customer_safeguards`, `c360_account`, `decisioning_customer` (and P4's
  `text_features`). Empty or missing → that step waits.

- [x] **Coverage: promises and open cases get a golden ID**
  ```sql
  WITH x AS (SELECT source_key, golden_customer_id FROM gold.customer_xref
             WHERE source_system = 'crm' AND status IN ('linked', 'singleton'))
  SELECT 'promises' AS what, count(*) AS n, count(x.golden_customer_id) AS linked
  FROM curated.promises_to_pay p LEFT JOIN x ON x.source_key = p.crm_customer_id
  UNION ALL
  SELECT 'open cases', count(*), count(x.golden_customer_id)
  FROM curated.collections_cases c LEFT JOIN x ON x.source_key = c.coll_customer_ref
  WHERE c.case_status = 'open';
  ```
  Expect close to 100% except the orphans. Write down how many of the 2,568 orphan open cases are now linked.

- [x] **Merges look sane**
  ```sql
  SELECT n_crm, count(*) AS customers FROM (
    SELECT golden_customer_id, count(*) FILTER (WHERE source_system = 'crm') AS n_crm
    FROM gold.customer_xref GROUP BY 1) GROUP BY 1 ORDER BY 1;
  ```
  Spot-check 20 customers with 2+ CRM records: same person (date of birth, national-ID hash)? Any cluster with more
  than 5 CRM records should not exist (P1's conflict rule).

- [x] **Deposits and bureau are linked**
  ```sql
  SELECT source_system, status, count(*) FROM gold.customer_xref GROUP BY ALL ORDER BY 1, 3 DESC;
  ```
  Write down the linked share for `deposit` and `bureau`: it decides whether Step 5's payday, liquidity and bureau
  features are worth trying.

- [x] **Label matches P3's** (broken or partially kept = 1, kept = 0)
  ```sql
  SELECT count(*) AS resolved,
         count(*) FILTER (WHERE e.label_broken IS DISTINCT FROM (p.ptp_status IN ('broken', 'partially_kept'))) AS mismatches
  FROM gold.promises_enriched e JOIN curated.promises_to_pay p USING (ptp_id)
  WHERE p.ptp_status IN ('kept', 'broken', 'partially_kept');
  ```
  Must be 0 mismatches. Otherwise raise it with P1 before Step 1.

- [x] **Outcome columns are split correctly**: `python -m pytest tests/test_interface_stubs.py -q`.

- [x] **`decisioning_customer` history grain**
  ```sql
  SELECT count(*) - count(DISTINCT (golden_customer_id, valid_from)) AS duplicate_rows,
         count(*) FILTER (WHERE available_from < valid_from) AS known_before_true,
         max(available_from) AS latest_known
  FROM gold.decisioning_customer;
  ```
  Need 0, 0, and `latest_known` on or before 2026-09-28.

- [x] **Sensitivity labels cover the gold columns**: `python -m trustissues.factory.ingest.dictionary`, then
      `git diff config/sensitivity.yaml`. Every gold column P3 will read must be `none`.

## Step 0b. Interface gaps (agree with P1 / P4 before Step 1)

- [ ] `gold.customer_safeguards` has, or P1 adds: `consent_push`, `internal_do_not_call`, `represented`, `insolvency`
      (bankruptcy **or** consumer proposal), `sunday_contact_permitted`, `permitted_call_start_local`,
      `permitted_call_end_local`, `preferred_contact_window_start`, `preferred_contact_window_end`, `time_zone`, and consent
      as booleans (or an agreed value list).
- [ ] `gold.promises_enriched` has `reminder_scheduled_flag` and keeps `crm_customer_id` next to `golden_customer_id`.
- [ ] P4 confirms the `ptp_intent_strength` values and the `confidence` range in `gold.text_features`.
- [ ] Agreed: case-level flags (hardship, plan, vulnerable, test cell, contacts in 7 days) stay on the case.
- [x] Any column not added yet: keep reading it from curated through `customer_xref` (note it in `p3_plan.md`).

---

## Step 1. Sources point at gold (1–2 h)

- [x] `src/trustissues/features/sources.py`:
  - `promises` → `gold.promises_enriched`, keeping today's output column names; `ptp_status`, `status_ts`,
    `amount_paid_against` stay masked until known (`known_at`).
  - add `customer_xref` and `c360_account` sources (`use="features"` / `"rules"` as they are used).
  - `crm_safety_flags` → `gold.customer_safeguards` (plus curated for any gap column from Step 0b).
  - update the module docstring.
- [x] `python -m pytest tests -q` (the source, sensitivity and leakage tests must pass).
- [x] `make features` and compare row counts with the curated run.

**Done when:** tests green; spine and feature row counts match the curated run for promises that have a golden ID.

## Step 2. The customer becomes the golden customer (2–3 h)

- [x] Spines carry `golden_customer_id`: `features/build_training_set.py`, `features/build_case_training_set.py`,
      `features/compute.py` (`register_spine`, `register_case_spine`).
- [x] Customer-keyed SQL joins on it: `promise_history.sql`, `channel_response.sql`, and the customer fallbacks in
      `delinquency.sql`, `liquidity.sql`, `payday.sql` (accounts through `c360_account`).
- [x] `nba/queue.py`: `golden_customer_id` in `gold.nba_decisions` is the real golden ID.
- [x] Feast: customer entity → `golden_customer_id` in `feature_repo/entities.py`; `cd feature_repo && feast apply`.
- [x] `make train` (no new features yet), then `make nightly`.

**Done when:** holdout AUC and calibration no worse than before beyond noise (case 0.838, promise 0.950);
`tests/test_feast_offline.py` green.

## Step 3. Rules and contact at the customer level (2 h)

- [x] `nba/rules.py`, `load_case_flags`: safeguards per golden customer, the most restrictive value across their
      records, combined with the case's own flags.
- [x] **One contact per person per day**: in `nba/queue.py` after allocation, keep the most valuable contact per
      `golden_customer_id` and turn the others into `wait` with blocked reason `customer_already_contacted_today`.
- [x] 7-day contact cap counted per customer, not per case.
- [x] Tests: two cases of one customer get one contact; most restrictive consent wins; the cap is per customer.
- [x] `make nightly`; record how many same-day duplicate contacts were removed and how many former orphan cases are
      now contactable.

**Done when:** tests green; numbers recorded.

## Step 4. Hardship across the customer (1–2 h)

- [x] `nba/allocate.py` `assign_owners`: one certified owner per golden customer across all their cases.
- [x] `nba/queue.py` `load_cases` / `load_uses_12m`: programme uses in 12 months and broken plans counted across the
      customer's accounts; months on book from the customer's oldest relevant account.
- [ ] If `gold.text_features` exists and P4's text evaluation passed: hardship level `clear` from a stated hardship
      signal (agreed confidence threshold); evidence sentences into `DecisionCard.evidence`; only notes dated before the
      as-of date.
- [x] `python -m pytest tests/test_hardship_monotonic.py tests/test_nba_decisions.py -q`.

**Done when:** hardship tests green (never harsher, no reminders); change in support reasons and programmes recorded.

## Step 5. New features, one group at a time (3–5 h; drop first if short of time)

For each group: write the SQL file in `features/sql/`, add it to `compute.GROUPS` / `CASE_GROUPS` (this puts it under
the leakage test), run the experiment protocol from `p3_plan.md` ("Promise model experiments"): two time folds, AUC
overall and among partial-amount promises, AUC without the amount features, calibration by month. **Keep the group only
if it helps on both folds**; otherwise delete it and record the result.

- [ ] Payday v2 (`salary_credit_history`): days from promise due date to next expected salary, salary lateness.
- [ ] Liquidity v2 (`deposit_accounts`, `transactions`): balance vs promise amount, overdraft use, returned items.
      **Never** `transactions.ptp_id_matched`.
- [ ] Bureau (`bureau_history` / `external`): 90-day score change normalised by `risk_score_model`; skip rows flagged
      `dq_inquiries_inconsistent` / `dq_hit_without_score`.
- [ ] Customer (`decisioning_customer`, joined on `available_from` before the event): tenure, product count, pay
      frequency, income verified. Proxy-screen each first.
- [ ] Text (`gold.text_features`, latest before the event, with confidence), only after P4's evaluation gate.

**Done when:** each group is either adopted (with numbers) or recorded as tried and dropped.

## Step 6. Retrain and refresh (1–2 h)

- [x] `make train`, then `make nightly`.
- [x] If monitoring still shows the case model's calibration at "watch" (it was −0.084 in July), recalibrate on the
      most recent fully observed month and record before / after.
- [x] Update the numbers in `p3_plan.md` and `p3_submission_text.md`.

**Done when:** `reports/model_evaluation.md` regenerated; new numbers in the docs.

## Step 7. Governance gates, before anyone uses the new queue (1 h)

- [x] **Fairness**: protected attributes joined through `customer_xref` (still only in `nba/fairness.py`);
      `python -m trustissues.nba.fairness`. No new calibration flag for our models; the "not in CRM" group shrinks to
      the unresolved cases; re-check the small language groups flagged on 3 Oct.
- [x] **Proxy audit** (in the same report): any new feature group pushing an attribute's proxy AUC above 0.60 is
      reviewed, and dropped if the outcome check shows that group treated differently.
- [x] **Monitoring**: reset the drift reference to the new holdout; `python -m trustissues.governance.monitoring`.
- [x] **Leakage**: `python -m pytest tests/test_feature_leakage.py -q` with every new group included.

**Done when:** all four reports regenerated and clean (or every flag explained in `p3_plan.md`).

## Step 8. App and cards (1 h)

- [x] Decision card: the golden customer, all their open cases, evidence quotes, hardship history across cases.
- [x] Agent queue: one row per customer per day when they have several cases.
- [x] `python -m pytest tests/test_agent_view.py tests/test_app.py -q`; open `make app` and click through one agent and
      one team lead.

**Done when:** pages work for an agent and a team lead on the gold data.

---

## Finish

- [ ] `make test` green; `make nightly` clean; `warehouse/p3/nightly.log` shows the timings.
- [ ] `p3_plan.md` has a dated section per step with the numbers; `p3_submission_text.md` numbers updated; tell P4 about
      changes for the README "what changed" table.
- [ ] Commit per step; push `p3/features-model`.

**Short on time?** Do Step 0 → 1 → 2 → 3 → 6 → 7 only. That gives real golden IDs, one contact per person per day, the
orphan cases resolved and the governance checks re-run. Step 5 is the first to drop, then Step 8, then Step 4.
