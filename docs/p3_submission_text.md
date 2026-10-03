# P3: Submission Text for TeamName_SystemDesign.pdf

Paste the two sections below into the design doc under these exact headings. Together they fill roughly half a page,
which leaves room for the other headings within the 4-page limit. **Rewritten 3 Oct (evening) to match what is built**:
every claim below is in the code on `p3/features-model`, with the numbers from the 28 Sep run; what is not built is
labelled as a next step. Details and evidence: `docs/p3_plan.md`, `reports/model_evaluation.md`,
`reports/fairness_report.md`, `reports/monitoring.md`.

---

## Business value

**Metric.** We improve **promise-kept rate**: the share of promises to pay that are paid in full by the due date plus
grace days, exactly as certified in `metric_definitions`. Secondary metrics are cure rate (paid up and closed within 30
days), 1–30 → 31–60 roll rate (down) and contacts per dollar recovered (down).

**How NBA moves it.**
- **The right action for every open case each morning**, valued as the expected extra recovery minus its cost, so
  cheap channels the customer actually responds to are used first and calls go where they earn their cost.
- **Agent time moved** from customers who would pay anyway to customers whom contact helps, within the day's real
  channel and agent capacity.
- **Hardship customers helped first:** a certified agent offers an eligible programme instead of an automated payment
  reminder (POL-COLL-004).
- *Next:* a "promise coach" during the call (break risk for a payday-aligned date or a smaller amount).

**How we measure it.**
- *Hackathon:* our models against the bank's on the same rows with a time-based holdout. Case risk (not cured in 30 days):
  AUC **0.839 vs 0.725** for the bank's `cure_30d`, better calibrated (calibration error 0.085 vs 0.251, and 0.015 when
  calibrated on the latest month, as the model now is). Promise break risk (broken; a partly kept promise is not broken): **0.968 vs 0.712** for `ptp_break`; most of that lead comes from the promised share of the overdue amount, which
  in this data almost decides the outcome. The value of a contact comes from the bank's own randomised test
  (contacted cells vs the no-contact hold-out): +0.62 points of cure per extra contact, an interval that includes zero,
  so the queue is deliberately conservative.
- *Production:* NBA runs as a challenger cell with a permanent 5% hold-out. Complaints, opt-outs and the hardship-offer
  rate are tracked as guardrails; `monitoring.py` tracks accuracy, calibration, drift and the override rate.

## Layer 3: AI Decisioning (Next Best Action)

**Who it decides for.** The customer is the golden customer from identity resolution: one person across their CRM,
card, loan and deposit records. Safeguards, the 7-day contact limit and hardship history are the person's, a person is
contacted at most once a day, and 1,443 open cases whose CRM record does not exist can now be worked (before: 6).

**When it decides.** Every open case gets one recommendation each morning in a batch (86,059 cases; the queue takes about
33 seconds after the data pipeline), shown to agents in the Agent Queue and Decision Card pages. *Next:* re-scoring on events (a payment,
a broken promise) and in-call help.

**Rules first.** Deterministic rules run before any model, so a model can never recommend a forbidden action:
- *Hard stops:* cease contact, deceased, insolvency, open dispute, third-party representation; the bank's randomised
  no-contact hold-out is left untouched.
- *Permissions:* channel consent (default deny), the internal do-not-call list (the national list covers telemarketing
  only, POL-COLL-001 §5.3), the 7-day contact cap, a contact window for the day (07:00–21:00 local, Sundays 13:00–17:00
  where permitted, within the customer's preferred window) and no collection contact on statutory holidays.
- *Hardship and vulnerability:* no payment reminders or collections calls. A certified agent owns every such case and
  calls to offer an eligible programme first when one is due; programmes come from a rules engine over
  `hardship_programs`, each cited to its policy section. Automated tests guarantee a hardship or vulnerability signal
  can never make treatment harsher.

**Models.** Two calibrated LightGBM models on point-in-time features (tested against look-ahead): the chance an open
case is **not paid up within 30 days** (every case), and the chance an open **promise breaks** (cases with one). Features:
the case so far, arrears and repayment, liquidity, salary credits, promise history and channel responsiveness. Protected
attributes, age, vulnerability and hardship flags are never model inputs. *Next, once records are linked across systems:*
deposit and bureau features, and LLM-extracted signals from notes and calls.

**Choosing the action.** Each permitted action, including "wait", is valued as effect × the case's risk × its response on
that channel × amount overdue − unit cost, with the effect from the bank's randomised test. Actions are then allocated
within the day's channel capacity and each working agent's calls, support calls first.

**Explanation.** Every decision card shows: the top three reasons (SHAP), why the action beat the runner-up, which rules
ruled out the others, the eligible hardship programmes, and when the customer may be called. Plain template text, no
LLM. *Next:* the note or call behind each text signal.

**Human review.** The assigned agent accepts or overrides every call with a reason code. Team leads sign off the daily
digital-reminder batch in one step; hardship, vulnerability and high-balance actions need explicit approval. Every
decision is logged and summarised for the weekly override review. Fairness is checked by group on testing data only.

---

## Hand to teammates (not for the PDF)

**P4 (architecture diagram).** Layer 3 must show:
- Tools: feature definitions in SQL with a Feast registry (offline store; **no Redis**, removed: nothing real-time reads
  it), LightGBM ×2 (case risk, promise break), SHAP, rules engine, hardship eligibility engine, capacity allocator,
  review and approvals, fairness check, monitoring.
- AI used at: the two models and the action valuation. **No LLM in Layer 3 today** (card wording stayed template;
  LLM text features come from P4 when built).
- Human review at: agent accept/override, team-lead batch approval, mandatory approval for hardship, vulnerability
  and high balance.

**P4 (Governance and AI usage headings).**
- Governance points: hardship and vulnerability are routing inputs, never model inputs, and tests guarantee they only
  make treatment gentler; protected attributes are read only by the fairness check (enforced by a test); the fairness
  report (no queue disparities; our models better calibrated across groups than the bank's); the proxy audit; age
  excluded; the bank's own policies applied (do-not-call, contact hours, hardship first).
- AI usage: the table in `p3_design_sections.md`, Heading "AI Usage", minus LLM card wording.

**P1 / P2 (Scalability heading).** Layer 3 lines to add:
- *Batch:* the morning run (`scripts/nightly.py`): pipeline, then the queue for every open case (33 s), fairness (12 s)
  and monitoring (2 s).
- *Next:* event re-scoring and the in-call promise coach, which would need an online feature store again.

**P1 (decisioning view).** Exclude age and age band, `id_document_type`, `interpreter_used_flag`, voice acoustic features
and benefit-type categories. P3 now reads the gold layer and keys everything by golden ID (`docs/p3_plan.md`, golden records
Steps 0-7). P3's label is P1's `label_broken` (partially kept is not broken). `customer_safeguards` now carries
the preferred contact window and time zone; `promises_enriched` still lacks the reminder flag (read from curated).

**P2.** Add `gold.nba_decisions` and `gold.nba_overrides` to the Ask schema graph. Use P3's feature definitions for the
drivers dimensions. The Data Quality page can call `trustissues.governance.monitoring.snapshot()`.

### Changes from the design (for the README "what changed" section)

| Design said | Built | Why |
|---|---|---|
| One model: chance a promise breaks | Two: case risk (every open case) and promise break | Only a third of open cases have an open promise |
| Action value from past results of each treatment | Effect from the bank's randomised test | Past results are biased: the bank contacts the cases that are not curing |
| Feast with a Redis online store | Feast registry with the offline store only | The batch queue computes features from the same SQL; nothing reads Redis |
| Hardship: gentler collections actions | No reminders; a certified agent's support call offering programmes | POL-COLL-004 §4.2 (offer before asking for payment) and §4.5 |
| National Do Not Call List blocks calls | Only the internal list | POL-COLL-001 §5.3: the national list covers telemarketing only |
| LLM wording of the card with a fact check | Template text | Time; the template is already plain language |
| Promise coach, event re-scoring, language rule | Not built | Time; listed as next steps |
| Policy lift by off-policy evaluation | Not done; per-contact effect from the randomised test instead | Time |
