# P3: Submission Text for TeamName_SystemDesign.pdf

Paste the two sections below into the design doc under these exact headings. Together they fill roughly half a page, which leaves room for the other seven headings within the 4-page limit. The full working spec is in `p3_design_sections.md`.

---

## Business value

**Metric.** We improve **promise-kept rate**: the share of promises to pay that are paid in full by the due date plus grace days, exactly as certified in `metric_definitions`. Secondary metrics are cure rate, 1–30 → 31–60 roll rate (down) and contacts per dollar recovered (down).

**How NBA moves it.** There are three levers:
- **Realistic promises at capture:** while the agent is still on the call, a "promise coach" shows estimated break risk for a payday-aligned date, a smaller amount or an instalment split.
- **Best-value action before the due date** on risky promises.
- **Agent time moved** from customers who would pay anyway to customers whom contact actually helps.

**How we measure it.**
- *Hackathon:* our model vs the incumbent break-probability model in `model_scores`, on the same promises with a time-based holdout (AUC, calibration, share of broken promises caught in the top 20% of risk). Policy lift is estimated with off-policy evaluation on the bank's champion / challenger / hold-out test cells.
- *Production:* NBA runs as a challenger cell with a permanent 5% hold-out. Complaints, opt-outs and the hardship-offer rate are tracked as guardrails.

## Layer 3: AI Decisioning (Next Best Action)

**When it decides.** Agents and team leads see a recommendation for every open case each morning (batch, refreshed on events such as a payment or a broken promise). They also get real-time help when a customer is making a promise on a call.

**Rules first.** Deterministic rules run before any model, so a model can never recommend a forbidden action:
- *Hard stops:* cease contact, deceased, insolvency, third-party representation, open dispute.
- *Permissions:* channel consent, 7-day contact caps, permitted hours, statutory holidays, language.
- *Hardship:* any sign of hardship or vulnerability routes the case to a certified agent. A rules engine over `hardship_programs` shortlists eligible programmes, each cited to its policy section.

**Model.** A calibrated LightGBM model predicts the chance a promise is broken. It uses point-in-time features: delinquency history, prior broken promises, the gap between due date and payday, salary pattern, liquidity, bureau trend and channel responsiveness, plus LLM-extracted signals from notes and calls (hardship, stated reason for being late, promise intent, dispute mentions). A hardship signal always routes the case to hardship help, and automated tests guarantee it can never lead to harsher treatment.

**Choosing the action.** Each permitted action, including "wait", is valued as expected payment gain minus contact cost, using outcomes from the bank's test cells. Actions are then allocated within that day's channel and agent capacity.

**Explanation.** Every decision card shows four things:
1. The top three reasons, from SHAP values.
2. Why the chosen action beat the runner-up.
3. Which rules blocked other actions.
4. The source note or call behind each text signal.

An LLM words the card for readability. An automatic check rejects any wording whose facts differ from the model's, and the plain template is shown instead.

**Human review.** Agents accept or override every call recommendation with a reason code. Team leads approve the daily digital-reminder batch. Hardship, vulnerability, near-threshold and high-exposure cases need explicit approval. Every override is logged and reviewed weekly.

---

## Hand to teammates tonight (not for the PDF)

**P4 (architecture diagram).** Layer 3 must show:
- Tools: feature store, LightGBM, SHAP, rules engine, eligibility engine, capacity allocator, LLM wording with fact check.
- AI used at: LLM text features, the model, action valuation, LLM wording.
- Human review at: agent accept/override, team-lead batch approval, mandatory approval for hardship and vulnerability.

**P4 (Governance and AI usage headings).**
- Governance points: the one-way door (hardship signals are model inputs, but tests guarantee they can only make treatment gentler), the proxy audit, fairness checks by group (testing only), and age excluded.
- AI usage: the table in `p3_design_sections.md`, Heading "AI Usage".

**P1 / P2 (Scalability heading).** Layer 3 lines to add:
- *Batch:* 06:00 daily queue and LLM wording of the cards.
- *Real time:* in-call promise coach, roughly 20–60 ms with no LLM on the path.
- *Near real time:* event re-scoring that cancels pending reminders after a payment.

**P1 (decisioning view).** Exclude age and age band, `id_document_type`, `interpreter_used_flag`, voice acoustic features and benefit-type categories. Provide point-in-time history views keyed by golden ID.

**P2.** Add `gold.nba_decisions` to the Ask schema graph. Use P3's feature definitions for the drivers dimensions.
