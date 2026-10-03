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
| `pyproject.toml` | Add an optional `p3` dependency group: `lightgbm`, `shap`, `scikit-learn`, `pandas`, `pyarrow`, `feast`. |

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
| `feature_repo/feature_store.yaml` | File offline store over `warehouse/p3/feature_views/*.parquet`. |
| `feature_repo/entities.py` | `ptp_id`, `crm_customer_id` for now (`golden_customer_id` later). |
| `feature_repo/feature_views.py` | One feature view per SQL file in step 3. |
| `tests/test_feast_offline.py` | Feast's point-in-time retrieval returns the values `features/compute.py` wrote. |

**Done when:** `feast apply` works and historical retrieval matches the computed features.

**Redis online store removed (3 Oct).** It was built (`docker-compose.yml`, `features/materialize.py`, an
offline = online parity test) but nothing read it: the morning queue is a batch job that computes its features
from the same SQL files, so serving them from Redis changed no decision and added a service that had to be
running. An online store is only worth it for real-time scoring (the promise coach, re-scoring on a payment),
which is not built.

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

**The full switch-over plan, written 3 Oct evening, is in [`p3_golden_plan.md`](p3_golden_plan.md)**: first checks, interface gaps
to close with P1 and P4, the steps in order with effort, new feature groups and the governance gates. The table below is
the original summary.

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

Steps 0–7 are built. The `Makefile` wraps the runs below (`make help`; `make train` retrains everything, `make
nightly` is the morning run, see "Morning run" at the end). P3 now reads `curated.*` (`config/settings.yaml` → `p3.source_schema`), because raw loses 10% of `account_monthly_snapshot.month_end_date` (non-ISO formats that curated recovers). Run order:

```bash
python -m trustissues.features.build_training_set   # step 1
python -m trustissues.nba.evaluate                  # step 2: incumbent
python -m trustissues.features.compute              # step 3
python -m trustissues.nba.train                     # step 4
(cd feature_repo && feast apply)                     # step 5: Feast registry (offline only)
python -m trustissues.features.build_case_training_set && python -m trustissues.features.compute --case
python -m trustissues.nba.train --case              # case model vs the bank's cure_30d
python -m trustissues.nba.actions                   # step 7 + calibration from the randomised test
python -m trustissues.nba.queue                     # NBA: one decision per open case (add --write-gold to write gold.nba_decisions)
python -m trustissues.nba.fairness                  # reports/fairness_report.md (after train and queue)
python -m trustissues.governance.monitoring         # reports/monitoring.md, and snapshot() for the Data Quality page
streamlit run app/Home.py                           # the app: Agent queue and Decision card pages (needs --write-gold)
python -m pytest tests -q
```

| Step | Result |
|---|---|
| 1 Spine | train 127,642 · valid 12,140 · holdout 32,869 (= every resolved promise the incumbent scored). Broken rate 36.9% / 51.4% / 61.4% |
| 2 Incumbent | AUC **0.695**, Brier 0.229, top-20% capture 27.0% (ceiling at a 61% broken rate is 32.5%) |
| 3 Features | 44 features (45 until `snapshots_observed_12m` was dropped, see the fairness check), 6 SQL files, leakage test passes (and catches an injected 1-day look-ahead) |
| 4 v0 | AUC **0.950**, Brier 0.086, top-20% capture 32.5%. **Without the promise-amount features: AUC 0.689**, level with the incumbent. See below |
| 5 Feast | `feast apply` works; historical retrieval = computed features for 20 promises × 6 views. Redis online store removed (step 5) |
| 6 Rules, hardship | Pure logic with tests (hard stops, consent default-deny, contact cap, hours; hardship never harsher, programme eligibility) |
| 7 Action data | `warehouse/p3/action_values.parquet` + report section |
| NBA queue | `nba/actions.py` (values), `nba/allocate.py` (capacity), `nba/explain.py` (reasons, summary), `nba/model.py` (scoring), `nba/queue.py`: 86,059 open cases → one `gold.nba_decisions` row each, with a `DecisionCard`; every case has a case-model risk. `--write-gold` tested on a scratch DuckDB, not yet run on the shared warehouse |
| Review | `nba/review.py` (I7): the assigned agent accepts or overrides (reason code required to override); a team lead approves or rejects (hardship and vulnerability: also a hardship specialist; roles from `agents.role`); the digital batch is signed off in one step; each recommendation's state (`ready`, `waiting_agent`, `waiting_approval`, `blocked`, `overridden`) and the weekly override summary. Append-only into `gold.nba_overrides`. On the 28 Sep queue: 13,343 ready, 784 calls waiting for their agent, 35,912 digital batch waiting for one sign-off, and 36,020 hardship / vulnerability / high-balance cases each waiting for an approval before the hardship routing fix, **8,306 after it** (see below); 12,612 after the Do Not Call fix |
| Fairness | `nba/fairness.py` → `reports/fairness_report.md`: both models (ours and the bank's, same holdout rows) and the morning queue compared across gender, age band, marital status, citizenship, newcomer programme, accessibility needs, dependants and preferred language, plus a proxy audit. Protected attributes are read only here (`sources.protected_attributes`, enforced by a test). See "Fairness check (3 Oct)" below |
| Monitoring | `governance/monitoring.py` → `reports/monitoring.md` and `snapshot()` (a dict for the Data Quality page): realised AUC and calibration by month for ours and the bank's models, score and feature drift (PSI) against a like-for-like reference, the bank's own PSI, queue mix, override rate once review decisions exist, P1 identity reviews and P4 text-feature accuracy once those exist. Found a live-scoring bug on its first run, see "Monitoring (3 Oct)" |
| Submission text | `docs/p3_submission_text.md` rewritten 3 Oct to match what is built (claims checked against the code and the 28 Sep numbers; promise coach, event re-scoring, LLM card wording, language rule and off-policy evaluation removed or labelled next), plus a "changes from the design" table for the README |
| Morning run | `scripts/nightly.py` (`make nightly`): Layer 1 pipeline, P4 text features (skipped until built), queue written to `gold.nba_decisions`, fairness, monitoring. P3 stages take 47 s; timings logged to `warehouse/p3/nightly.log` |
| Pages | `app/pages/2_Agent_Queue.py`, `app/pages/3_Decision_Card.py` on `nba/agent_view.py` (data, tested without Streamlit) and `nba/review.py` (decisions). `gold.nba_decisions` written to the warehouse for 28 Sep (86,059 rows). See "Agent Queue and Decision Card pages (3 Oct)" |
| Case model | `features/build_case_training_set.py`, `features/sql/case_state.sql`, `nba/train.py --case`: AUC 0.838 vs the bank's `cure_30d` 0.725 |

New facts from the data (add to §2):

| Fact | Consequence |
|---|---|
| `account_monthly_snapshot` is **quarterly** (quarter-ends 2023-12-31 to 2026-06-30) | "12 months" = up to 4 snapshots; features renamed `*_last_snapshot`, `snapshots_*_12m`. Promises before 2024 have no snapshot features (~32% null) |
| `customer_stated_income_date` always equals `ptp_due_date` when present | The planned due-date-vs-income-date gap is always 0; only a "stated an income date" flag is kept |
| `ptp_to_overdue_ratio` nearly determines the outcome (full-amount promises rarely break) and dominates v0 | Checked it is creation-time, not back-filled: `overdue_at_creation` matches the creating contact ~91% in every status, broken promises also have ratio 1, and the full-amount share falls across fully resolved months (49% → 15%, May–Aug 2026). The drift also explains the jump in break rate since May 2026. Treat v0's 0.95 as a property of this synthetic data; the ablation (0.690) is the measure of the other features |
| Only ~40% of champion/challenger cases have `assignment_method = random` | Cell comparison uses random cases only. Result: champion and both challengers are within 0.1 pt (16.0–16.1% paid in 30 days), no-contact 15.2% |
| `prior_broken_ptp_count` = earlier broken promises in the same case, all resolved before creation | Safe to use as-is |
| Promise outcomes follow the promised amount, not the customer's history (`data_quality_report.md` T2, T4) | Full-amount promises break ~2%, promises for 90–99.9% of the amount break 100%, below that the rate rises with the share promised. Without the amount features, AUC among partial promises is ~0.54 |

### Promise model experiments (3 Oct): nothing beat v0, code not kept

Tested on two time folds: before the shift (train before 2026, validate Jan–Feb 2026, test Mar–Apr 2026) and the
official holdout. AUC overall / among partial promises:

| Variant | Before the shift | Holdout |
|---|---|---|
| v0 (45 features) | 0.959 / 0.862 | **0.951 / 0.902** |
| + explicit full-amount flag and shortfall in CAD | 0.959 / 0.861 | 0.951 / 0.902 |
| + monthly repayment record (card statements, loan instalments, chequing payroll and balance) | 0.958 / 0.859 | 0.950 / 0.900 |
| + promise behaviour (share paid on earlier promises, agent's broken rate over 180 days, case contact in 7 days) | 0.958 / 0.856 | 0.949 / 0.898 |
| all of the above, without the snapshot-timing features | 0.958 / 0.858 | 0.949 / 0.898 |
| same, simpler trees (15 leaves, 500 per leaf) | 0.956 / 0.852 | 0.947 / 0.895 |
| v0 without amount features | 0.638 / 0.505 | 0.690 / 0.543 |
| + repayment + behaviour, without amount features | 0.646 / 0.508 | 0.695 / 0.534 |

Among partial promises every new feature scores 0.50–0.57 on its own (latest card utilisation 0.62, the best).
In this data the customer's record barely predicts whether a promise is kept; the amount does. v0 is kept as it
is: it already calibrates well on the shifted holdout (calibration error 0.017), so recalibrating on recent
weeks would not help it (the case model is where recalibration matters, `data_quality_report.md` T1). Below the
90% cliff v0 still scores 0.888 against the bank's 0.691, all of it from the amount features.

### NBA decision logic (v1)

For each open case: rules → case risk → value each allowed action → hardship routing → capacity → card → approval.

- **Risk (every case)**: the case model `case_nocure_v0`, the chance an open case is *not* paid up and closed
  within 30 days. Rows = every case open at a month-end since Jan 2024 (246,673); features = `case_state.sql`
  plus the shared promise-history, arrears, liquidity, payday and contact groups. Holdout AUC **0.838 vs 0.725**
  for the bank's `cure_30d` on the same case-months, better calibrated too. Point-in-time tested (incl. the
  day-k arrears, which are only used once known). The promise model still scores open promises; its break
  risk is shown on the card.
- **Value** = effect × (risk / average risk) × reach × amount overdue − unit cost.
  - *effect*: +0.62 points of cure per extra contact (95% interval −2.1 to +3.4), from the bank's randomised
    test (random cases: contacted cells vs `no_contact_holdout`, which gets 0.46 fewer treated contacts per
    case). Historical "rate after treatment" numbers are **not** used: by cure, every treatment looks worse
    than waiting, because the bank contacts the cases that are not curing.
  - *reach*: the customer's own response on that channel in the last 30 days (answered calls, opened emails,
    clicked SMS links, opened pushes) relative to the average, shrunk halfway to average.
  - *cost*: `channel_capacity.unit_cost_cad`, or `call_cost_cad` in settings for calls.
- **Hardship and vulnerability** (`nba/hardship.py`, POL-COLL-004; rewritten 3 Oct, see below): a flagged case
  gets no payment reminder and no collections call. It gets `wait`, or a `support_call` (a certified agent
  offering help, eligible hardship programmes first, no payment request before an option is offered) when one
  is due: no programme offered yet, a plan ending within 10 days, a follow-up 30+ days after an offer that was
  not taken up, or a vulnerable customer who would otherwise be contacted today. Every contact rule still
  applies to it. Every flagged case has a certified owner. Eligible programmes go on the card.
- **Capacity** (`nba/allocate.py`): support calls first (most exposed first), then the most valuable cases, into
  the day's channel capacity and each working agent's outbound calls (assigned agent → same team → anyone;
  certified agents only for hardship); a case whose best action is full gets its next best and the card says so
  (`no_capacity`). Flagged cases without a certified agent get one as owner (`assign_owners`).
- **Experiment**: cases in the bank's `no_contact_holdout` cell are left to the bank (rule `experiment_holdout`).
- **Contact hours, Sundays, holidays** (`rules.contact_window`; added 3 Oct, see below): calls and texts only inside the
  day's window (07:00–21:00 local, Sundays 13:00–17:00 where permitted, narrowed to the preferred window); no collection
  contact on a federal statutory holiday except a posted letter. The window is on the card.
- **Do not call** (rule `do_not_call`, phone only): the bank's internal list, i.e. the customer asked the bank not to
  call. The National Do Not Call List is not used: it covers telemarketing only (POL-COLL-001 §5.3; fixed 3 Oct,
  see below).
- **Approval** (first that applies), only when something is done, never for `wait`: vulnerability, hardship,
  balance ≥ `high_balance_cad` (5,000), digital batch. Recorded with `nba/review.py`.
- **Placeholders until gold**: `golden_customer_id` holds `crm_customer_id`; `evidence` is empty until P4's
  `gold.text_features`; hardship level is `clear` when an agent or a customer form recorded it and `possible`
  when only a model flagged it (P4's text features can raise it later).
- **Names** follow the team's sample card: actions `agent_call`, `sms_reminder`, `email_reminder`, `push_reminder`,
  `letter`, `wait`, plus `support_call` (added 3 Oct); channels `phone`, `sms`, `email`, `mail`, `push`; rules include `consent` and `contact_limit`.
- **Open with the team**:
  - `risk_score` in `gold.nba_decisions` now holds the case risk (stub comment says "probability promise breaks").
  - Call cost: at $2.40 per call only ~800 calls are worth making and ~61k agent calls on shift go unused.
    If agent time on shift is already paid, set `call_cost_cad` to the true extra cost.
  - ~~`reason_code` list for overrides~~: settled in `nba/review.py`, see `interfaces.md` question 5.

### Hardship routing fix (3 Oct)

**What was wrong.** Hardship cases were capped at the gentlest *collections* actions, so they got automated
payment reminders (email, push) and could never get a call, even from a certified agent offering help. That
asks for payment before offering a programme (POL-COLL-004 §4.2). The card said "route to a certified agent",
but most of these cases had no agent at all (§4.5). Programme eligibility was built and tested but never used by
the queue. Every flagged case needed an individual approval, 36,020 a day with high balance included.

**What changed.**
- `nba/actions.py`: new action `support_call` (phone, harshness 0, never valued with the collections effect).
- `nba/hardship.py`: flagged cases get `wait` or `support_call` only; `support_reason` / `waiting_reason` say why;
  hardship level from `hardship_source`; eligibility also checks `max_uses_12m`; `options` gives the card entries.
- `nba/queue.py`: loads the hardship history (plan end, offers before the as-of date, broken plans, uses in 12
  months) and months on book (card / loan / deposit opening dates, new rule sources in `features/sources.py`);
  approval never for `wait`; the report section lists the flagged cases by situation.
- `nba/allocate.py`: support calls placed first; `assign_owners` gives every flagged case a certified owner.
- `nba/explain.py`: card text for each support reason and each reason to wait.
- `common/interfaces.py`: `DecisionCard` gains `support_reason` and `hardship_options` (optional, see `interfaces.md`).
- `nba/review.py`: a support call, like any call, needs its agent's acceptance.
- `reports/data_quality_report.md` L11: 688 offers and 1,645 answers are dated after the snapshot; the queue ignores
  them (an offer counts only if made before the as-of date, its answer only if given before it).

**Effect on the 28 Sep queue (35,648 flagged cases).**

| | Before | After |
|---|---|---|
| Payment reminder or collections call | 23,408 | 0 |
| No agent at all | 29,032 | 0 (all owned by an active certified agent) |
| Individual approvals (flagged cases) | 35,648 | 8,051 (support calls only) |
| Support calls | 0 | 8,051: 3,830 first offers, 2,340 vulnerable, 1,739 follow-ups, 142 plan reviews |
| Waiting, nothing due | – | 17,879: 10,372 on a plan, 6,217 offered in the last 30 days, 1,290 vulnerable with no contact due |
| Waiting, call due but blocked by a rule | – | 9,718: do not call 4,901, consent 2,251, contact limit 687, dispute 672, representative 552, cease contact 386, experiment holdout 268, insolvency 1 |

Individual approvals across the whole queue are now 8,306 (hardship 4,484, vulnerability 3,567, high balance 255),
plus one sign-off for the 35,912-case digital batch. Breakdown regenerated on every run in
`reports/model_evaluation.md` ("Hardship and vulnerable cases").

**Still open.**
- ~~4,901 due support calls are blocked by `do_not_call`~~: fixed the same day, see "Do Not Call fix" below.
- 2,251 are blocked by phone consent: a hardship letter or email offering the programmes would reach them.
- `broken_plan_90d` is approximated (offers carry no break date): a plan accepted in the last 90 days that is
  now broken.
- Hardship cases on a running plan wait; payments on the plan are not checked yet.

### Do Not Call fix (3 Oct)

**What was wrong.** `nba/rules.py` blocked every phone call for customers on the National Do Not Call List as well
as the bank's internal list. The bank's own policy says the national list "applies to telemarketing only and does
not block collection calls" (POL-COLL-001 §5.3, FAQ-COLL-001 Q2); 356,436 CRM customers are on it, against 14,801
on the internal list.

**What changed.** `do_not_call` now means the internal list only (`internal_do_not_call_flag`);
`national_dncl_flag` is no longer read (`features/sources.py`). Documented in the `nba/rules.py` docstring; test
`test_only_the_internal_do_not_call_list_blocks_calls`.

**Effect on the 28 Sep queue.**

| | Before | After |
|---|---|---|
| Cases with a call blocked by `do_not_call` | 22,851 | 950 |
| Collections calls (`agent_call`) | 812 | 1,254 |
| Support calls (hardship / vulnerable) | 8,051 | 12,357 |
| Support calls due but blocked by a rule | 9,718 | 5,412 (consent 2,251, contact limit 1,066, dispute 672, representative 552, cease contact 386, experiment holdout 268, internal do not call 216, insolvency 1) |
| Individual approvals | 8,306 | 12,612 (more support calls to approve) |

4,306 flagged cases moved from `wait` to `support_call`; about 600 other cases changed channel (mostly to a call
or a letter). Every support call fits in the certified agents' capacity, and 48,417 agent calls on shift are
still unused. The contact-limit count rises because those calls are no longer blocked by do-not-call first.

### Fairness check (3 Oct)

`python -m trustissues.nba.fairness` writes `reports/fairness_report.md` (12 seconds). Protected attributes come from
the CRM through one source, `sources.protected_attributes` (`use="fairness"`), the only source allowed protected
columns; a test fails if any module other than `nba/fairness.py` reads it. The report shows aggregates of groups of at
least 100 people. It is a screen for review (thresholds in the module docstring), not a legal test. A model flag
needs the difference to pass the threshold **and** be at least 1.96 standard errors (two-proportion test for the
ratios, sampling error for calibration; AUC only with 100+ rows of each outcome): without that, groups of a few
hundred rows flagged and unflagged at random between two retrains of the same model. Vulnerability is not compared:
it is a safeguard that deliberately changes treatment.

**What it found on the 28 Sep data** (after the retrain below).
- **Morning queue: no flags.** For cases without a hardship or vulnerability flag, every group gets collections
  contact at 1.00–1.06 times what its risk alone predicts. Raw contact rates differ (marital status 33–50%) because
  hardship and vulnerability flags differ by group, and those cases get support calls instead.
- **Our models are better calibrated across groups than the bank's.** The bank's case score understates the risk of
  separated customers by 0.31 (overall 0.25) and of customers 65+ by 0.18; its promise score understates separated
  customers' by 0.16. Flags: promise model 5 ours / 3 the bank's; case model 8 ours / 2 the bank's. The bank's flags
  are calibration errors; most of ours are selection differences.
- **Most of our flags follow real outcome differences**: e.g. 18.0% of 55–64s are in our case model's riskiest 20%
  against 22.8% of 25–34s, and their actual not-cured rates are 73.0% and 82.9%. Each flag shows the actual rates.
- **To review** (they go against the outcomes, in small groups):
  - Promise model, Arabic speakers (197 rows): it catches 33.1% of their broken promises against 45.7% for Punjabi
    speakers, although they break more often (69.0% vs 59.9%), and its score is 0.053 below the actual rate.
  - Case model, Punjabi speakers (912 rows): 18.2% in the riskiest 20% against 24.7% of Arabic speakers, although
    they fail to cure more often (80.4% vs 75.5%).
  - Case model, 3+ dependants (170 rows): 14.1% in the riskiest 20% against 22.6% with 2 dependants, at similar
    actual rates (78.2% vs 81.7%).
- **Proxy audit:** the case model's features predict newcomer-programme membership with AUC 0.643, every other
  attribute 0.50–0.58. See the follow-up below.
- **Not a protected group, but found here:** 2,568 open cases point at a CRM customer that does not exist (DQ check
  R1). They get no contact at all (no consent record, so the rules default to deny), and the case model understates
  their risk (gap −0.146 vs −0.087 overall) because the customer-keyed features are missing. Identity resolution
  (the golden record) fixes both.

**Follow-up: `snapshots_observed_12m` removed (3 Oct).** The first run of the proxy audit put this feature (how many
quarterly account snapshots the customer had in the 12 months before) among the top carriers of newcomer status
(AUC 0.653). It also tracks the data period: the snapshot history starts at the end of 2023, so 21% of 2024 rows
have all four snapshots against 65% in 2025. Retrained both models without it:

| | With it | Without it |
|---|---|---|
| Promise model holdout AUC (calibrated) | 0.9506 | 0.9499 |
| Case model holdout AUC | 0.8373 | 0.8382 |
| Newcomer proxy AUC, case model features | 0.653 | 0.643 |
| Newcomer proxy AUC, promise model features | 0.722 | 0.713 |

No accuracy lost, so it was removed from `features/sql/delinquency.sql` (one definition, so from both models and
the Feast view); 203 of 86,059 queue decisions changed. But the proxy barely moved, and the first explanation was
wrong: newcomers have *more* snapshots on average (2.76 vs 2.22), not fewer. No single feature carries newcomer
status (removing any one of the top eight leaves the proxy at 0.72–0.73). For the promise features much of it is
timing: the newcomer share of customers rose from 7.6% to 11.5% over 2025–26, so the date alone predicts it at 0.663.
Within a single quarter the features still reach 0.66–0.68, spread over ordinary financial features (amounts,
balances). Removing features cannot take that out without removing the model; the safeguard is the outcome check
above, which shows both models calibrated for newcomers (no newcomer calibration flag for ours; the bank's promise
score has one).

### Monitoring (3 Oct)

`python -m trustissues.governance.monitoring` (2 seconds) writes `reports/monitoring.md`; `snapshot(con)` returns the
same content as a dict for the Data Quality page. Status per model is the worst of three checks: score drift (PSI
of today's scores against a like-for-like reference: below 0.10 stable, 0.10–0.25 watch, 0.25+ act), the latest
month's AUC against the holdout's (0.03 / 0.05 lower = watch / act), and its calibration gap (0.05 / 0.10). The
queue now saves a feature snapshot for the promise model too (`FS-<date>-ptp_break_v0.parquet`; the one in the
folder was stale, from an older queue), so monitoring reads exactly what the queue scored. Drift reference for the
promise model: the 6,659 promises open on 30 Jun (the first holdout day), not all resolved holdout promises, which
differ from today's open promises by construction.

**Bug found on the first run, fixed.** Today's open cases all showed *no* open promise (0.0%, against 16.1% at every
historical month-end). Promises still open carry `status_ts` = their creation time, and the case features and the
case spine treated a promise as open only if `status_ts` was at or after the scoring time: right for history (every
promise there has resolved since, so `status_ts` is its resolution), wrong for live scoring. Now a promise is resolved
only once it has a final status recorded before the scoring time (`features/sql/case_state.sql`,
`features/compute.py`). Training features are unchanged (only floating-point noise of 1e-16), so the models were not
retrained. On the 28 Sep queue: 30,946 open cases (36%) now carry their open promise, mean case risk 0.674 → 0.716,
408 decisions changed (mostly to a call), and 20,254 cards that told the agent "No open promise to pay" for a
customer who had one are corrected. Test: `test_a_promise_still_open_counts_as_open_when_scoring_today` (fails on
the old code). The hardship and Do Not Call tables above were measured before this fix; their totals move by a few
cases (e.g. support calls 12,357 → 12,359).

**Status on 28 Sep: act.**
- `ptp_break_v0`: **act** for drift, and it is real. Today's open promises are almost all for part of the amount
  (0.3% for the full amount, against 28.5% of the promises open on 30 Jun), so the mean predicted break risk is 0.90
  against 0.57 (score PSI 2.2; `ptp_to_overdue_ratio` PSI 0.75). This is DQ check T2 at its extreme. Accuracy and
  calibration on the months already resolved are fine (Aug: AUC 0.941, gap +0.023). Next: judge September once its
  promises resolve.
- `case_nocure_v0`: **watch** for calibration: it understates the not-cured rate by 0.084 in July (DQ check T1, the
  April shift). Drift is ok (score PSI 0.042). The bank's `cure_30d` understates it by 0.271.
- The bank's own records: `ptp_break` PSI 0.27 (act); its other nine models below 0.10. The bank writes PSI per row, with up to ~60 different values on one day for the same model, so monitoring uses that day's median (picking one row made the result change from run to run).
- Override rate, identity reviews (P1) and text-feature accuracy (P4) show "not available yet" until
  `gold.nba_overrides` has decisions, `gold.identity_review_queue` exists and `reports/text_feature_eval.md` is written.

### Channel fix (3 Oct)

`contact_history.channel` is empty from 26 Sep 2026 (upstream rename to `channel_v2`, DQ check K1); the curated
layer adds `channel_resolved` = coalesce of the two, and the DQ report says to use it. `features/sources.py` read
`channel`, so the contact features scored today lost the channel of the last two days of contacts (19,462 contacts,
10,462 of them calls). The contacts source now reads `channel_resolved` (output name unchanged, so no feature SQL
changed; it exists only in curated, not raw). Training rows are unaffected (the case month-ends end on 31 Jul and the
promise model trained on promises before 30 Jun), so the models were not retrained. On the 28 Sep queue: calls in
the last 30 days 2.08 → 2.20 per case on average, cases with a call-response rate 64.4% → 67.2%, 588 decisions changed.

### Agent Queue and Decision Card pages (3 Oct)

`streamlit run app/Home.py` (install with `pip install -e ".[app]"`; `.streamlit/config.toml` turns off Streamlit's
usage statistics). The pages read `gold.nba_decisions`, so the queue must have been written to the warehouse first:
`python -m trustissues.nba.queue --write-gold` (done for 28 Sep: 86,059 rows; the gold interface tables were created
by `apply_stubs` at the same time). `app/Home.py` is a minimal placeholder so the pages run; the plan gives Home and
Ask to P4.

- **Acting as** (sidebar, every page): the demo login. Lists team leads first, then staff with work today.
- **Agent queue.** An agent or hardship specialist sees their own cases in working order (support calls, calls,
  other contact, waits) with risk, value, state and the one-line summary; selecting a row opens its card. A team lead
  sees where the day stands, signs off the digital reminder batch in one click, works the individual approvals
  (hardship, vulnerability, high balance) and sees the weekly override review. A hardship specialist also sees the
  hardship and vulnerability approvals.
- **Decision card.** State and approval badges; risk, value and channel; the summary; the top reasons as bars; why
  this action and the runner-up; the eligible hardship programmes with their policy sections; what was ruled out and
  by which rule; evidence quotes (empty until P4's text features); the case's facts, last 10 contacts and last 5
  promises before the as-of date; and the decision: Accept / Override (with a reason code) for the assigned agent,
  Approve / Reject (with a reason code) for an approver, with the decisions so far.
- **Who sees what** (`nba/agent_view.py`): agents see only cases assigned to them; team leads, hardship specialists
  and QA analysts see every case. `nba/review.py` re-checks every decision (only the assigned agent accepts; only
  the right role approves).
- **Data shown:** IDs, case facts, contacts and promises through `features/sources.py`, so no PII or protected
  attribute can reach the page (the source tests apply).
- **Write lock:** the app holds the warehouse's write lock while it runs (it writes `gold.nba_overrides`); stop it
  before running the Layer 1 pipeline or `--write-gold`.
- **Checked by hand in the browser:** team lead queue and digest, opening a card from the approvals table, approving
  a support call (state moved to "Agent to decide", since a call also needs its agent's acceptance), and a hardship
  specialist's queue. The test approval was deleted afterwards. Tests: `tests/test_agent_view.py` (order, access,
  history, digest, as-of cut-off) and `tests/test_app.py` (every page runs on the real warehouse as a lead and as an
  agent, Streamlit's AppTest).

### Morning run (3 Oct)

`python scripts/nightly.py` (or `make nightly`) runs, in order: the Layer 1 pipeline (`factory/pipeline.py`, P1), P4's
text features (skipped while `features/text_extract.py` does not exist), the queue with `--write-gold`, the fairness
report and monitoring. Each stage is its own process; the run stops at the first failure and appends the timings to
`warehouse/p3/nightly.log`. Options: `--skip-pipeline`, `--from <stage>`, `--only <stage>`, `--dry-run`. Measured on
28 Sep without the pipeline: queue 32.7 s, fairness 12.3 s, monitoring 2.0 s (the pipeline itself took about 4.5 min
in its last logged run). Retraining is deliberately not nightly: `make train` rebuilds the training sets, features,
the incumbent baseline, both models, the action values and the Feast registry. The queue writes to the warehouse, so
the app must be stopped first. `make` targets: pipeline, features, train, queue, fairness, monitoring, nightly, app,
test. Test: `tests/test_nightly.py`.

### Contact hours, Sundays and holidays (3 Oct)

**What was wrong.** The permitted-hours rule only fired when a planned contact time was passed in, and the morning
queue (a plan for the day) never passed one, so it never fired. There was no Sunday or statutory-holiday rule, although
POL-COLL-001 §5.1 has both.

**What changed** (`nba/rules.py`). Each case gets a contact window for the planned day (`contact_window`): the policy's
07:00–21:00 customer-local within the CRM's permitted hours (07:00–21:00 for everyone in this data); on Sundays 13:00–17:00,
and only if `sunday_contact_permitted` (256,581 CRM customers do not permit it); narrowed to the customer's preferred
window when they overlap (it always overlaps here; a preference can never widen the legal hours). Calls and texts with
no window are blocked (`no_sunday_contact`, `outside_permitted_hours`). On a statutory holiday
(`settings.yaml` `p3.statutory_holidays`: the 2026 federal holidays, since banks are federally regulated) every contact
action is blocked except a letter, which can still be posted (`statutory_holiday`). A planned time, when given, is still
checked against the window. The window is on the card (`contact_window`, new optional field) and in the summary ("Call
the customer, between 08:00–17:00 customer-local"). Tests in `tests/test_rules_hard_stops.py`.

**Effect.** On Monday 28 Sep no decision changes (every customer has a window); the cards now say when to call. Planning
Sunday 27 Sep instead: 15,977 of the 86,059 open cases would lose calls and texts, the rest limited to 13:00–17:00.
Planning Wednesday 30 Sep (National Day for Truth and Reconciliation): 79,466 would get no contact but a letter (the
rest are already blocked by other rules).

**Cease contact and letters: kept as it is.** POL-COLL-001 §5.4 lets *required* letters and statements continue after a
cease-communication request. The `letter` action is a payment-reminder letter, not a required one, so it stays blocked;
required letters and statements are sent outside NBA. Recorded in the `nba/rules.py` docstring.


### Golden records, Step 0: checks before switching (3 Oct)

Runbook: [`p3_golden_runbook.md`](p3_golden_runbook.md). `main` merged into `p3/features-model` (P1's identity, gold and
publish gate), warehouse rebuilt with the full pipeline (`verify_warehouse.py` all pass, publish gate passed with two
warnings Q6/Q7). Building it OOM-killed the laptop once: DuckDB is now capped (`settings.yaml` `duckdb:`) and
`make pipeline` / `make nightly` run in a 16 GB systemd scope (`requirements.md` troubleshooting).

| Check | Result | Verdict |
|---|---|---|
| Gold objects | all present; `text_features` exists but is **empty** (P4 not run) | text waits (Steps 4 and 5) |
| Promises with a golden ID (CRM key) | 231,517 of 238,815 (96.9%) | ok, rest are orphans |
| Open cases with a golden ID | 83,477 of 86,059 by the CRM key; **86,050 through `gold.case_customer`** (account holder). Of the 2,582 orphans, 2,573 now have a person; 9 do not | use `case_customer`, not the CRM key, for cases |
| Merges | at most 2 CRM records per person (19,911 people); 0 disagree on date of birth or national-ID hash; 20 spot-checked, 3 with two surnames and one ID hash (name change) | sane |
| Linked share | deposit 100% (580,000), bureau 99.0%, card 99.5%, loan 99.7% | Step 5 payday, liquidity and bureau features are worth trying |
| Label | **7,549 mismatches** = exactly the `partially_kept` promises: P1's `label_broken` follows the certified `broken_promise_rate` (partially kept is not broken), P3's label follows the certified `promise_kept_rate` (partially kept is not kept). Both are bank metrics; P1's docs (`consumer_views.md` A) expect P3 to build its own | **P3 keeps its label**, from `promises_enriched.ptp_status`; never reads `label_broken`. To tell P1 |
| Outcome columns split | `tests/test_interface_stubs.py` 13 passed | ok |
| `decisioning_customer` grain | 0 duplicate rows, 0 known before true; latest `available_from` 2026-09-29 06:00 = the 28 Sep snapshot (one row per customer), available the next morning by P1's rule (month end + 30 h) | point-in-time is right; a 28 Sep queue cannot use that last snapshot (Step 5 only) |
| Sensitivity of gold columns | `sensitivity.yaml` labels source tables only; regenerating it changed nothing | Step 1: each gold column P3 reads is labelled by the curated column it comes from, and the test enforces it |

**Interface gaps (Step 0b).** `gold.customer_safeguards` has every consent and hours column except `represented`
(derived here from `third_party_representation`, `power_of_attorney`, `legal_representative`), the preferred contact
window and `time_zone`: those three stay read from `curated.customers` through `customer_xref`. Consents are `'true'` /
`'false'` text, NULL for the 15,182 people with no CRM record (treated as no consent). `gold.promises_enriched` keeps
`crm_customer_id` but has no `reminder_scheduled_flag`: read from curated by `ptp_id`. `gold.text_features` has the
agreed columns, no rows.

### Golden records, Step 1: sources read gold (3 Oct)

`features/sources.py` now reads P1's gold layer for everything about a person. A source can name its schema and
LEFT JOINs (`Join`, columns as `alias.column`), so a column gold does not have yet is joined from curated.

| Source | Now reads | Notes |
|---|---|---|
| `promises` | `gold.promises_enriched` + `curated.promises_to_pay.reminder_scheduled_flag` by `ptp_id` | adds `golden_customer_id` (238,796 of 238,815 promises; 231,517 through the CRM key). Label still from `ptp_status` |
| `customer_xref` (new) | `gold.customer_xref`, CRM records only | other systems' keys (card numbers, CIFs) are PII |
| `account_owner` (new) | `gold.c360_account` | account -> person, 1,844,500 accounts, for Step 2's customer joins |
| `crm_safety_flags` | `gold.customer_xref` -> `gold.customer_safeguards` + `curated.customers_current` (preferred window) | one row per CRM record carrying its person's safeguards. Same output columns, so `nba/rules.py` is unchanged until Step 3. `consumer_proposal_flag` now reads gold `insolvency` (consumer proposal or an insolvency hold on any of the person's cases) |
| `cases`, `case_history` | + `gold.case_customer` | add the case's `golden_customer_id` (367,217 of 367,229 cases) |

**Sensitivity.** `sensitivity.yaml` labels source tables only, so each gold column names the curated column it is
built from (`label_table`, `label_as`; the golden ID is labelled like `customers.crm_customer_id`), and the tests check
that label: `tests/test_p3_sources.py` adds `test_gold_columns_have_lineage`, and the customer-attribute rule now covers
`customer_safeguards` and joined tables. `sources.create_empty_tables` builds the all-VARCHAR fixture warehouse for
the tests that need one.

**Safety flags compared with the CRM-only version**, per CRM record (1,017,885): no protection lost (0 consents
gained, 0 cease-contact or vulnerability flags dropped, 0 Sunday permissions gained, 0 call hours widened). Gained,
because P1 combines all of a person's records, cases and contacts: 2,389 cease contact, 7,928 vulnerable, 2,703
insolvency.

**Result.** 191 tests pass. `make features`: training spine 172,651 and case spine 246,673 rows, as before; every
feature value equal to the curated run within 1e-9 (the 4,020 promise and 7,451 case rows an exact comparison flags
differ by float summation order only).

### Golden records, Step 2: the customer is the golden customer (3 Oct)

**What changed.** Every customer-keyed join uses `golden_customer_id`. The promise and case spines carry it (from
`promises_enriched` and `case_customer`), and so do every feature file and Feast view (`feature_views/*.parquet`,
`features.parquet`, `case_features.parquet`, with `crm_customer_id` kept beside it for the fairness check). Sources:
`contacts` gets the person by P1's rule (the case's person, else the CRM record's; 99.96% of 2.69 M contacts);
`account_snapshots` gets the account holder from `gold.c360_account` (all 12.6 M rows, including the 2.6% whose CRM ID
does not exist). Feature SQL: `promise_history`, `channel_response`, and the customer fallbacks in `delinquency`,
`liquidity` and `payday` join on the golden ID. Source columns may now be an SQL expression with declared lineage.
`train.py` `NON_FEATURES` and the Feast `KEYS` exclude the new key; the Feast customer entity is
`golden_customer_id`. `nba/queue.py` writes the case's golden ID to `gold.nba_decisions` (the 9 open cases with no
person keep their CRM ID).

Identity is today's resolution applied to history: a merge found now is used for promises made before it. Accepted
(the links are facts about people, not outcomes), and noted here.

**Features.** Same rows and the same 44 / 52 features. 0–5% of rows changed per feature; most: `salary_snapshots_12m`
3.6% of promises (5.1% of case rows), `card_utilisation` 3.1% (4.4%), `contacts_30d` 3.0%. Deposits and cards now
reach the person when the promise names no account.

| Holdout | Before (curated) | After (golden) |
|---|---|---|
| Promise model AUC (calibrated) | 0.9499 | **0.9502** |
| Promise model Brier | 0.0861 | 0.0859 |
| Case model AUC | 0.8383 | **0.8398** |
| Case model Brier | 0.1276 | 0.1275 |
| Bank (ptp_break / cure_30d) | 0.6946 / 0.7250 | unchanged |

No worse anywhere, as the step requires. `tests/test_feast_offline.py` passes (its key list now has the golden ID).

**Queue** (`make nightly --skip-pipeline`, with Step 1's safeguards): support calls 12,359 -> 12,727, waits 33,702 ->
33,863, push 13,943 -> 13,581, email 16,103 -> 16,007, calls 1,497 -> 1,458. Mostly Step 1: 7,928 more people are
vulnerable through another of their records, so their cases go to certified agents. **Every open case belongs to a
different person** (86,050 people, plus the 9 unresolved cases), so a same-day duplicate contact cannot happen on
this snapshot; Step 3 still adds the rule. Fairness: case model 6 screening flags (was 8); a new one for the promise
model, preferred language TL (riskiest-20% ratio 0.64, small group). Monitoring: `card_utilisation` drift PSI 0.26
(was 0.24, now "act"), as cards newly link to the person. Both for Step 7.

### Golden records, Step 3: rules and contact at the customer level (3 Oct)

**What changed.** `nba/rules.py` `load_case_flags` reads the safeguards of the case's golden customer (one row per
person: any flag set, any consent withheld, the latest start and earliest end of the permitted and preferred hours),
OR-ed with the case's own flags as before. A case whose CRM ID does not exist now gets its person's safeguards
(through `gold.case_customer`); a person with no CRM record still has no consent. The 7-day contact count is the
person's outbound contacts on any of their cases in the 7 days before the planned day, never fewer than the case's
own `contacts_last_7d` (which matches that definition for 93% of open cases). `nba/queue.py` `load_cases` reads
vulnerability per person. **One contact per person per day** (`one_contact_per_customer`, before capacity is
allocated, so freed capacity goes to other customers): a support call wins, else the most valuable contact; the
person's other cases wait, with `customer_already_contacted_today` in the blocked actions and the other case named
on the card. Tests: `test_every_case_of_a_person_gets_the_persons_safeguards`,
`test_the_7_day_contact_cap_counts_the_persons_contacts_on_every_case` (`tests/test_rules_hard_stops.py`),
`test_two_cases_of_one_person_get_one_contact_the_most_valuable`,
`test_a_support_call_wins_over_a_more_valuable_reminder_for_the_same_person` (`tests/test_nba_decisions.py`). 195 pass.

**Effect** (`make nightly --skip-pipeline`, 28 Sep):

| | Before (Step 2) | After |
|---|---|---|
| Same-day duplicate contacts removed | n/a | **0**: every open case belongs to a different person on this snapshot |
| Open cases whose CRM ID does not exist (2,582) that get a contact | 6 | **1,443** (355 support calls, 431 email, 350 push, 161 SMS, 108 letters, 38 calls); 1,139 still wait |
| Cases blocked by the 7-day contact limit | 4,555 | 6,204 (contacts to the same person through their other, closed cases) |
| Queue | wait 33,863, email 16,007, push 13,581, support 12,727, SMS 5,423, letter 3,000, call 1,458 | wait 33,532, email 16,160, push 13,695, support 12,721, SMS 5,482, letter 3,000, call 1,469 |

### Golden records, Step 4: hardship across the customer (3 Oct)

**What changed** (`nba/queue.py`, `nba/allocate.py`). Hardship history is the person's: offers and a plan broken in
90 days (`load_cases`) and programme uses in 12 months (`load_uses_12m`) count across all of the person's cases
(person = golden customer, or the CRM ID for the 9 cases with none). Months on book run from the person's oldest
account in the same product system as the case's account (card, loan or deposit, across all their records, through
`gold.c360_account`); POL-COLL-004 does not define months on book, and programmes are per product, so "relevant" is
read as the same product system. One certified owner per person: `allocate.assign_owners` takes the person
(`Request.customer`) and keeps an owner once chosen, starting from the owners capacity allocation already gave.
Test: `test_one_certified_owner_per_person_across_their_cases`. **Not done:** hardship from text
(`gold.text_features` has no rows yet; P4).

**Effect** (28 Sep): no action changed; hardship tests green (never harsher, no reminders). Programme options changed
on 926 cards: 153 more cases have at least one programme (28,451 -> 28,604), 1,644 more options in all, as months on
book now count the person's oldest account. Support-call reasons: `offer_programme` 5,893 -> 5,772, `follow_up` 2,574
-> 2,695 (121 people were already offered a programme on another case).

### Golden records, Step 6: retrain, recalibrate, refresh (3 Oct)

`make train` on the golden features (Steps 3 and 4 changed rules, not models), then `make nightly --skip-pipeline`.
Monitoring still had the case model's calibration at **watch** (-0.094 in June, -0.084 in July: the not-cured rate
climbs every month after the April break, 54.5% / 67.5% / 76.6% / 82.9% for the Apr-Jul month-ends), so it is now
recalibrated on the most recent fully observed month-end (`p3.case_model.recalibrate_on_latest`, `nba/train.py`):

| Calibration of `case_nocure_v0`, judged on Jul 2026 | AUC | Brier | ECE |
|---|---|---|---|
| fitted on valid (Apr-May), as before | 0.8629 | 0.1129 | 0.0842 |
| fitted on Jun only (the test of the switch) | 0.8633 | 0.1022 | **0.0139** |

The saved model is calibrated on July (31 Aug outcomes run past the last recorded cure, 15 Sep). Holdout results
(valid calibration, for comparison with before): case AUC **0.8405** vs the bank's 0.7250, promise 0.9501 vs 0.6946.
Monitoring: case calibration **ok** (gap +0.010 in June, out of sample; 0.000 in July, the fitting month).
Queue: mean case risk 0.715 -> 0.811, close to the true not-cured rate; calls 1,469 -> 1,837 (higher honest risk makes
more calls worth their cost), waits 33,532 -> 33,459. `p3_submission_text.md` updated (numbers, golden customer, the
open points for P1).

### Golden records, Step 7: governance gates (3 Oct)

**Fairness** (`nba/fairness.py`): protected attributes are joined to the PERSON: `protected_attributes` per CRM record,
through `customer_xref` to the golden ID that model rows, the queue and the proxy audit (one row per person) carry; a
person with several CRM records takes each attribute from the first record that has it. Since Step 2 the queue rows
carried golden IDs while the groups were keyed by CRM ID, so that section was wrong until this step. The report's
section 4 now describes people with no CRM record; `tests/test_fairness.py` also checks no golden ID is printed.

| | Before golden (3 Oct) | Now |
|---|---|---|
| Screening flags, promise model (ours / bank's) | 5 / 3 | 5 / 2 |
| Screening flags, case model (ours / bank's) | 6 / 2 | 7 / 2 |
| "Not in CRM" holdout rows, promise / case | 1,042 / 2,202 | **127 / 256** |
| Those case rows: share in our riskiest 20%, calibration gap | 4.3%, -0.143 | 24.2%, -0.026 |
| Queue cases with no CRM record (no consent: all wait) | 2,568 | 322 |
| Queue screens (contact, contact vs risk, support calls) | no flags | no flags |

No new calibration flag for our models: the case model's 65+ gap is the same flag as on 3 Oct (about 0.06 from
overall then, -0.029 vs -0.088, and now, +0.065 vs +0.004). The extra case flag is age band selection, ratio 0.79 for
55-64 vs 35-44, on the 0.8 line (it was in the 3 Oct report, gone in Step 2, back now). The small language groups
from 3 Oct: AR calibration -0.053 (n=203) persists; TL is now flagged on bad outcomes caught only (0.73, was also
selection 0.66). Bank's newcomer calibration flag gone. **Proxy audit:** newcomer programme still predictable from
the case features (AUC 0.644, was 0.646: entry_overdue, balance_to_limit, card_utilisation); no new feature group was
added (Step 5), so nothing new to review.

**Monitoring:** the drift reference is built from the current holdout on every run, so the retrain reset it.
`case_nocure_v0` **ok** (recalibrated, Step 6). `ptp_break_v0` **act** on score drift, as on 3 Oct (PSI 2.25, was
2.18: today's open promises are a different mix from resolved ones, DQ T2); feature drift `balance_to_limit` 0.35
(was 0.34) and `card_utilisation` 0.26 (new: cards now reach the person), `ptp_to_overdue_ratio` 0.75.
**Leakage:** `tests/test_feature_leakage.py` passes on every group (no group added). 196 tests pass.

### Golden records, Step 8: app and cards (3 Oct)

**Decision card** (`app/pages/3_Decision_Card.py`, `nba/agent_view.py` `customer_context`): the caption shows the
golden customer; a new panel "The customer (N cases)" lists all the person's cases (open first) and their hardship
offers on any of those cases, known before the as-of date, and opens by itself when the person has more than one case.
Evidence quotes still wait for P4's text features. **Agent queue** (`agent_queue`): one row per customer (the contacted
case, or the first in working order) with `other_cases`; the metric reads "Customers". On 28 Sep every open case is a
different person, so the lists do not change. Tests: `test_agent_queue_order_and_scope` (a second case of the same
person is folded into its row), `test_customer_context_lists_the_persons_cases_and_offers`. 197 tests pass.

**Clicked through** (`make app`): team lead AG-1294 (today's states, batch, approvals; opened the card of G-0773844,
5 cases, 3 earlier offers across them) and agent AG-1681 (queue with golden IDs, card with Accept / Override). **Bug
found and fixed** (`app/ui.py`, there since the pages were added): Streamlit drops a widget's value on a page change,
so opening a card from the agent's queue switched "Acting as" back to the first member of staff (a team lead), and the
card refused the assigned agent's decision. The choice now lives in a plain session key copied into the picker.
