# P3 work plan: features and model, before curated and gold land

**Owner:** P3 (Aman) · **Branch:** `p3/features-model` · **Written:** 3 Oct, ~13:30 IST

This covers what P3 builds now, while Anushka speeds up and pushes the curated build. It follows [`implementation_plan.md`](implementation_plan.md) §5–6 and uses the same file names. Everything here runs on the **raw** warehouse (`raw.*`, built by `load_raw`) and is written so the switch to `gold.*` later is a one-file change.

---

## 1. Why P3 isn't blocked

Promises, cases, contacts and account snapshots already carry `crm_customer_id` and/or `account_id`. Only deposits (`src_customer_ref`) and bureau (`bank_subject_ref`) need identity resolution. So the training set, the baseline comparison, most features, the model and the rules can all be built today.

## 2. Facts from the data (checked on raw)

| Fact | Consequence |
|---|---|
| `metric_definitions.promise_kept_rate` = `kept / (kept + partially_kept + broken)`, excluding `open` and `cancelled` | **Label = 1 if `broken` or `partially_kept`**, 0 if `kept`. Drop `open` (33,005) and `cancelled` (1,441). ~204k labelled promises. |
| Incumbent `model_scores.model_name = 'ptp_break'` exists only for score dates 2026-06-30 to 2026-09-28: 66,121 promises, **32,869 of them resolved** | **Time split at 2026-06-30.** Train on promises created before; the holdout is the 32,869 resolved promises the incumbent scored. |
| 130k of 239k promises were created in 2026 | Recent-heavy. Keep a validation slice from the months just before the split for early stopping. |
| `account_id` is null for 68k promises (case-level or `multi`); 160,880 match `account_monthly_snapshot` | Account features at account level when present, otherwise rolled up by `crm_customer_id`. LightGBM handles the nulls. |
| `account_monthly_snapshot` has `crm_customer_id` and `salary_credit_amount` | A first payday/salary feature is possible **before** identity. Replace it with the deposit-based version once gold exists. |
| `test_cell` in `collections_cases` and `contact_history`: champion 250k, challenger_A 55k, challenger_B 55k, no_contact_holdout 7k | Source for action valuation (`nba/actions.py`). Answers the plan's §11 question. |
| `contact_history` has `rpc_flag`, `ptp_made_flag`, opened/clicked flags, `contact_cost_cad`, `treatment_code` | Channel response features and contact cost come from here. |
| Every raw column is `VARCHAR` | Cast inside our own typed views (step 0), not throughout the code. |

## 3. Ground rules for this work

- **One source module.** All P3 code reads through `features/sources.py`. Today it defines typed views over `raw.*`; later it points at `gold.promises_enriched` / `gold.decisioning_customer`. Nothing else names a raw table.
- **Explicit column allow-list.** Until `gold.decisioning_customer` exists, select only named columns, never `SELECT *`, and never from `customers` for model features. Check against `config/sensitivity.yaml`: no `protected` or `direct_pii` column may reach a feature. A test enforces this.
- **Point-in-time.** Every feature uses data dated strictly before `ptp_created_ts`. For monthly snapshots, use the latest `month_end_date` before the promise date. Never use `today()`; `AS_OF_DATE` comes from config.
- **Leakage list.** Never used as features: `ptp_status`, `status_ts`, `amount_paid_against`, `paid_date`, `broken_reason_agent_coded`. Also no `model_scores` column as a feature, since that's the model we're beating.
- **Work on our own database file.** P3 code opens the warehouse read-only. P3's outputs (training sets, model files) go to `warehouse/p3/` as Parquet files, not into the shared DuckDB, so P3 never holds the write lock.

## 4. Work items, in order

### Step 0: Typed source views and dependencies
| File | Intention |
|---|---|
| `src/trustissues/features/__init__.py`, `src/trustissues/nba/__init__.py` | Package stubs. |
| `src/trustissues/features/sources.py` | `promises()`, `account_snapshots()`, `contacts()`, `cases()`, `hardship_programs()`, `incumbent_ptp_scores()`: typed relations over raw with casts and only the allow-listed columns. The single place to swap to gold. |
| `pyproject.toml` | Add an optional `p3` dependency group: `lightgbm`, `shap`, `scikit-learn`, `pandas`, `pyarrow`, `feast`, `redis`. |

**Done when:** each function returns a typed DuckDB relation, and a test checks that no returned column is labelled protected or direct PII.

### Step 1: Training set spine (`features/build_training_set.py`)
- One row per labelled promise: `ptp_id`, `crm_customer_id`, `account_id`, `case_id`, `ptp_created_ts` (event time), `label`, `split` (`train` / `valid` / `holdout`).
- Split: `holdout` = resolved promises with an incumbent `ptp_break` score; `train` = created before 2026-06-30, except a validation slice of the last ~2 months before the split.
- Writes `warehouse/p3/training_spine.parquet`.

**Done when:** row counts per split and per label are printed, the holdout has 32,869 rows, and no holdout promise was created before any training promise.

### Step 2: Incumbent baseline (`nba/evaluate.py`)
- Score the bank's `ptp_break` on the holdout: AUC, calibration (reliability table plus Brier score), and the share of broken promises caught in the riskiest 20%.
- If a promise has several incumbent scores, use the first score dated on or after `ptp_created_ts`; log how many promises this affects.
- Written as a function `evaluate(scores, labels)` that the trained model reuses later. Writes `reports/model_evaluation.md`.

**Done when:** the incumbent's numbers are in the report. **That's the bar to beat**, and a number for the deck.

### Step 3: Feature SQL (`features/sql/*.sql` + `features/compute.py`)
Each SQL file takes the promise spine and returns `ptp_id` plus features, all point-in-time.

| File | Features | Source (now) |
|---|---|---|
| `promise_at_creation.sql` | `dpd_at_creation`, `prior_broken_ptp_count`, `ptp_to_overdue_ratio`, `overdue_at_creation`, `ptp_amount`, `days_to_due_at_creation`, `ptp_sequence_in_case`, `ptp_type`, `ptp_channel`, `payment_method_promised`, `reminder_scheduled_flag`, `grace_days`, `product` | `promises_to_pay` (known when the promise is made) |
| `promise_history.sql` | Promises broken in the last 90 days; promises made in 90 days; days since the last broken promise | Earlier promises of the same customer, resolved before the event time |
| `delinquency.sql` | Days past due at the last month-end; worst in the last 12 months; months delinquent in 12 | `account_monthly_snapshot` |
| `liquidity.sql` | Card utilisation (`balance / limit`); minimum due vs promise amount; payment made last month | `account_monthly_snapshot` |
| `payday.sql` | **Interim:** months with a salary credit in the last 6; change in salary amount; gap between the promise due date and the customer-stated income date | `account_monthly_snapshot.salary_credit_amount`, `promises_to_pay.customer_stated_income_date` |
| `channel_response.sql` | Right-party contact rate, promise-made rate, open/click rate by channel in the last 30 days; contacts in the last 30 days | `contact_history` |

`features/compute.py` runs the files, joins them onto the spine, and writes `warehouse/p3/features.parquet`. These same SQL files become the Feast sources in step 5, so each feature is defined once.

**Done when:** feature null rates and value ranges are printed, and a leakage test passes: shifting the event time one day earlier doesn't change features built from events after it.

### Step 4: First model (`nba/train.py`)
- LightGBM on `train`, early stopping on `valid`, then calibration (isotonic) on `valid`.
- Score the holdout with `evaluate()` from step 2 and add the result next to the incumbent in `reports/model_evaluation.md`.
- Quick SHAP check: global feature importance, to catch any leakage that looks too good.
- Saves `warehouse/p3/models/ptp_break_v0.txt` plus a JSON listing the features and their order.

**Done when:** v0's AUC, calibration and top-20% capture are reported next to the incumbent's. Beating it is not required for v0.

### Step 5: Feast skeleton
| File | Intention |
|---|---|
| `docker-compose.yml` | Redis only. |
| `feature_repo/feature_store.yaml` | File offline store over `warehouse/p3/*.parquet`; Redis online store. |
| `feature_repo/entities.py` | `ptp_id`, `crm_customer_id` for now (`golden_customer_id` later). |
| `feature_repo/feature_views.py` | One feature view per SQL file in step 3. |
| `features/materialize.py` | Pushes the latest values to Redis. |
| `tests/test_feature_parity.py` | Offline value equals online value for the same entity and timestamp. |

**Done when:** `feast apply` works, and one promise's features can be read from both stores and match.

### Step 6: Rules and hardship, pure logic plus tests
| File | Intention |
|---|---|
| `nba/rules.py` | Input: one case's flags (cease contact, deceased, insolvency/bankruptcy, open dispute, channel consent, contacts in the last 7 days, the customer's preferred contact window). Output: allowed actions, plus the rule that blocked each removed action. No AI. |
| `nba/hardship.py` | Hardship or vulnerability → route to a certified agent. Programme eligibility comes from `hardship_programs`, valid on the as-of date (`effective_from` ≤ date < `effective_to`), checked against `max_dpd`, `min_months_on_book`, `excludes_insolvency`, `excludes_broken_plan_90d`, `product_types`. |
| `tests/test_rules_hard_stops.py` | No contact action survives cease, deceased, insolvency or dispute. |
| `tests/test_hardship_monotonic.py` | Turning a hardship signal on never makes the recommended action harsher. |

Rules use case flags (`collections_cases`) and CRM flags (`customers`) by `crm_customer_id` for now. They switch to `gold.customer_safeguards` later. CRM safety flags are read **only** here, never as model features.

### Step 7: Action valuation data prep (`nba/actions.py`, first half)
- From `contact_history` / `collections_cases`, build a table by `treatment_code` × `test_cell` × DPD bucket: contacts, promise rate, payments in 30 days, average `contact_cost_cad`. `no_contact_holdout` gives the "wait" baseline.
- Writes `warehouse/p3/action_values.parquet` and a short summary in `reports/model_evaluation.md`.
- Choosing an action (expected payment minus contact cost) comes in hours 20–28 as planned.

## 5. Blocked until identity and gold, and what changes then

| Item | Waiting for | Change needed |
|---|---|---|
| Deposit balance vs promise amount; payday gap from `salary_credit_history` | Deposit → golden ID link | New SQL in `liquidity.sql` / `payday.sql`; the interim payday features stay or are dropped based on the model |
| Bureau score change over 90 days | Bureau → golden ID link | New `bureau.sql` (normalise by `risk_score_model` range) |
| Model view without protected attributes | `gold.decisioning_customer` | `sources.py` points at it; the allow-list test then checks against the view |
| Customer-level roll-ups across CRM duplicates | `gold.customer_xref` | Change the entity from `crm_customer_id` to `golden_customer_id` in `sources.py` and Feast |
| Text features (hardship, stated reason, promise strength, dispute) | P4 `gold.text_features` (I3) | New feature view; join on `source_id` / `source_ts` before the promise time |

## 6. To agree with the team

- **With Anushka (P1):** the columns P3 needs in `gold.promises_enriched` (everything in `promise_at_creation.sql` plus `golden_customer_id`) and in `gold.decisioning_customer`. Send her the step 3 tables once they're stable.
- **With P4:** the I3 shape already in the plan, plus `confidence`. Text extraction should cover promises in the training window and the holdout, not only open cases, or the model can't learn from them.
- **With P2:** `gold.nba_decisions` columns for Ask, once step 4 works.

## 7. Checkpoints

| By | Have |
|---|---|
| +1 h | Steps 0–2: spine and incumbent baseline numbers |
| +4 h | Step 3: features file with leakage test |
| +6 h | Step 4: v0 model compared with the incumbent |
| +8 h | Steps 5–6: Feast round-trip, rules and hardship with tests |
| Then | Step 7, and the switch to gold as soon as Anushka pushes curated, identity and gold |

## 8. Status (3 Oct, built and run on the full data)

Steps 0–7 are built. P3 now reads `curated.*` (`config/settings.yaml` → `p3.source_schema`), because raw loses 10% of `account_monthly_snapshot.month_end_date` (non-ISO formats that curated recovers). Run order:

```bash
python -m trustissues.features.build_training_set   # step 1
python -m trustissues.nba.evaluate                  # step 2: incumbent
python -m trustissues.features.compute              # step 3
python -m trustissues.nba.train                     # step 4
docker compose up -d redis && (cd feature_repo && feast apply) && python -m trustissues.features.materialize   # step 5
python -m trustissues.nba.actions                   # step 7
python -m trustissues.nba.queue                     # NBA: one decision per open case (add --write-gold to write gold.nba_decisions)
python -m pytest tests -q
```

| Step | Result |
|---|---|
| 1 Spine | train 127,642 · valid 12,140 · holdout 32,869 (= every resolved promise the incumbent scored). Broken rate 36.9% / 51.4% / 61.4% |
| 2 Incumbent | AUC **0.695**, Brier 0.229, top-20% capture 27.0% (ceiling at a 61% broken rate is 32.5%) |
| 3 Features | 45 features, 6 SQL files, leakage test passes (and catches an injected 1-day look-ahead) |
| 4 v0 | AUC **0.950**, Brier 0.086, top-20% capture 32.5%. **Without the promise-amount features: AUC 0.690**, level with the incumbent. See below |
| 5 Feast | `feast apply` + Redis materialize work; offline = online for 20 promises × 6 views |
| 6 Rules, hardship | Pure logic with tests (hard stops, consent default-deny, contact cap, hours; hardship never harsher, programme eligibility) |
| 7 Action data | `warehouse/p3/action_values.parquet` + report section |
| NBA queue | `nba/actions.py` (values), `nba/explain.py` (reasons, summary), `nba/model.py` (scoring), `nba/queue.py`: 86,059 open cases → one `gold.nba_decisions` row each, with a `DecisionCard`. 30,946 have an open promise and a risk score. `--write-gold` tested on a scratch DuckDB, not yet run on the shared warehouse |

New facts from the data (add to §2):

| Fact | Consequence |
|---|---|
| `account_monthly_snapshot` is **quarterly** (quarter-ends 2023-12-31 to 2026-06-30) | "12 months" = up to 4 snapshots; features renamed `*_last_snapshot`, `snapshots_*_12m`. Promises before 2024 have no snapshot features (~32% null) |
| `customer_stated_income_date` always equals `ptp_due_date` when present | The planned due-date-vs-income-date gap is always 0; only a "stated an income date" flag is kept |
| `ptp_to_overdue_ratio` nearly determines the outcome (full-amount promises rarely break) and dominates v0 | Checked it is creation-time, not back-filled: `overdue_at_creation` matches the creating contact ~91% in every status, broken promises also have ratio 1, and the full-amount share falls across fully resolved months (49% → 15%, May–Aug 2026). The drift also explains the jump in break rate since May 2026. Treat v0's 0.95 as a property of this synthetic data; the ablation (0.690) is the measure of the other features |
| Only ~40% of champion/challenger cases have `assignment_method = random` | Cell comparison uses random cases only. Result: champion and both challengers are within 0.1 pt (16.0–16.1% paid in 30 days), no-contact 15.2% |
| `prior_broken_ptp_count` = earlier broken promises in the same case, all resolved before creation | Safe to use as-is |

### NBA decision logic (v0)

For each open case: rules → break risk of its open promise (NULL if none) → value each allowed action →
hardship adjustment → card → approval.

- **Value** = risk × (30-day payment rate after the action − rate when waiting) × amount at stake − unit cost.
  Rates by treatment and DPD bucket from `action_values.parquet` (≥ 500 contacts), costs from
  `channel_capacity.unit_cost_cad`. The no-contact holdout exists only in bucket 1-30, where it pays like the
  weakest treatment, so elsewhere "wait" = the weakest treatment. Rates are observational: they rank actions,
  they don't prove uplift.
- **Approval** (first that applies): vulnerability, hardship, balance ≥ `high_balance_cad` (5,000), digital batch.
- **Agent**: calls and waits stay with the assigned agent; digital and letters go to batches; hardship/vulnerable
  cases with an uncertified agent get `agent_id` NULL (to be routed to a certified agent).
- **Placeholders until gold**: `golden_customer_id` holds `crm_customer_id`; `evidence` is empty until P4's
  `gold.text_features`; hardship level is `possible` from the case flag only.
- **Open with the team**: action names (fixture uses `agent_call`/`phone`, P3 uses `outbound_call`/`call`);
  `reason_code` list for overrides; capacity (≈19.8k calls recommended for ~790 active agents) is the stretch
  `allocate.py`.
