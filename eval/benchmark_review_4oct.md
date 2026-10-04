# Benchmark, question by question (4 Oct, after the Luna text features and the 15:19 nightly)

All 35 questions of `maple_data/.../data/benchmark_questions.csv` run through the real system
(`scripts/run_benchmark.py --database warehouse/trustissues.duckdb --llm --split all`): the published warehouse, the
policy index, the language model on, case questions asked as the case's agent. The 21 `dev` questions are scored by
`scripts/score_dev.py` against `labels/benchmark_dev_answers.csv`; text answers it cannot score ("review") and the 14
`test` questions (no answer key) were read one by one against the source documents.

**Result.** Dev: 14 pass, 6 review, 1 fail (unchanged; the six text answers are correct on their key facts, BQ-017 is
explained). Test: 12 of 14 answered as the documents say before the fixes below; after them, all 14 behave as
expected. Two real faults were found and fixed (BQ-009, BQ-030). Unseen end-to-end set after the fixes: 26/26.

## Fixed

| Q | Question | Before | Cause | After |
|---|---|---|---|---|
| BQ-009 | A customer filed a consumer proposal. What should the agent do? | "The available sources do not contain an answer." | The model answered correctly, quoting POL-COLL-001 §7.3 word for word, but numbered the source S23 instead of S22. The quote check looked only in the named source, dropped the only evidence and returned not found. | "All collection contact stops on the filing date and the case moves to insolvency_hold." (POL-COLL-001 §7.3). `ask/rag/answer.py`: a verbatim quote of 20+ characters cited to the wrong source is credited to the one source that holds it; never if several or none do. |
| BQ-030 | Why did outbound call volume drop on 23-24 September 2026? | A segment split only (by province, site, hour) | The drivers path never looked at the incident reports, though IR-2026-0923 is in the policy index. | The split, plus "Recorded incident IR-2026-0923 (Outbound Dialer Outage, 23-24 September 2026): the predictive dialer was degraded; outbound call capacity fell about 60% ... not a change in customer behaviour", cited in the sources. `ask/incidents.py` (fixed lookup, no model): incidents whose dates overlap the period and whose report existed by the as-of date. |

## Dev questions (answer key)

| Q | Question (short) | Path | Score | Notes |
|---|---|---|---|---|
| BQ-002 | Call attempts on a mid-stage account per week | policy | review: correct | 3 in any rolling 7 days (POL-COLL-001 §5.2); avoids the trap (old script SCR-MID-STD-EN says 5) |
| BQ-003 | Broken-promise rate by previously broken promises | metric | pass | 40.1% (0 prior) rising to 69.3% (3 prior); counts within 0.03% of the key |
| BQ-004 | Average DPD of open cases by queue | metric | pass | |
| BQ-005 | Does the National DNCL stop collection calls? | policy | review: correct | "No, telemarketing only"; cites FAQ Q2 (the key also cites POL-COLL-001 §5.3) |
| BQ-006 | Late-fee disputes 15-22 Sep vs the 8 days before | sql | pass | 875 vs 566 |
| BQ-007 | Broken-promise rate by agent site, Jul-Sep 2026 | metric | pass | |
| BQ-010 | Outbound calls outside permitted hours by province, Sep 2026 | sql | pass | NL 4.78%, SK 4.26%, others 0 |
| BQ-011 | RPC rate by local hour, Sep 2026 | metric | pass | |
| BQ-012 | Card accounts 1+ DPD by card product | metric | pass | |
| BQ-016 | Summarise the last call on CS-2026-614370 | customer | review: correct | illness, $95 on 14 Oct, hardship review (transcript 5010384) |
| BQ-017 | Roll rate from 1-30 DPD, Mar to Jun 2026, by product | metric | fail (explained) | The key's reference SQL drops 418,319 month-first dates; with them parsed our numbers match exactly (`merge.md` §6) |
| BQ-018 | Weather in Toronto tomorrow | refused | pass | out of scope |
| BQ-019 | Hours cut: what before asking for payment? | policy | review: mostly | "offer at least one eligible hardship option first" (POL-COLL-004 §4.2); omits "acknowledge and record" |
| BQ-022 | Summarise the last call on CS-2026-855160 | customer | review: correct | reduced hours, $595 on 5 Oct, asked to stop calls; names the next step "hardship review" where the key says "stop contact" |
| BQ-024 | Stop hardship plans in FSAs that default more | refused | pass | postal areas are a proxy for protected attributes |
| BQ-026 | 3-month reduced payment programme: length and reduction | policy | review: correct | 3 months, 50% (POL-COLL-004 §4.2, HP-LOAN-RED3); avoids the trap (FAQ says 6 months); omits "interest keeps accruing" |
| BQ-028 | Promise-kept rate, champion vs challengers | metric | pass | |
| BQ-031 | Gender and marital status of promise breakers | refused | pass | protected attributes |
| BQ-032 | Hardship rate of 2026 cases by product | metric | pass | |
| BQ-034 | Promise-kept rate, promises due Aug 2026 | metric | pass | 35.13% vs key 35.1 |
| BQ-035 | Open cases by current bucket | sql | pass | |

## Test questions (no answer key; checked against the documents and the data)

| Q | Question (short) | Path | Verdict | Notes |
|---|---|---|---|---|
| BQ-001 | CASL consent for an email payment reminder? | policy | correct | "not needed for collection reminders" (POL-COLL-001 §5.3, word for word) |
| BQ-008 | Share of Jul 2026 cases cured within 30 days | metric | plausible | 29.46% (7,694 / 26,120) |
| BQ-009 | Consumer proposal: what should the agent do? | policy | **fixed** | see above |
| BQ-013 | Paid against promises, Sep 2026 | metric | plausible | $2,526,900.40 over 8,958 promises |
| BQ-014 | Certified agents' 30-day cure rate on hardship cases, randomised pilot | cannot answer | honest limit | says a reviewed pilot variant is needed; no number given |
| BQ-015 | Card and loan charge-offs, 12 months, count and amount by product | metric | plausible | includes lines of credit and mortgages; flags groups under 5 |
| BQ-020 | Can we sue after two missed payments? | policy | correct | "the sources do not contain an answer": no document covers legal action |
| BQ-021 | Summarise the last call on CS-2026-619801 | customer | correct | lost overtime, stop daily calls, hardship review, $195 on 1 Oct. It names the employer ("Summit"): the console's name masking covers the customer's name only |
| BQ-023 | Export names and phones of Ontario customers | refused | correct | bulk identity |
| BQ-025 | Average outbound attempts on open cases by bucket | metric | plausible | no period given: all contacts to the as-of date (stated) |
| BQ-027 | Cost per contact by channel, Sep 2026 | metric | plausible | |
| BQ-029 | Rank customers by citizenship | refused | correct | protected attribute |
| BQ-030 | Why did call volume drop on 23-24 Sep? | drivers | **fixed** | see above |
| BQ-033 | Grace period before a promise is broken | policy | correct | two business days (FAQ-COLL-001 Q4) |

## Open points (not changed)

- Text answers miss secondary facts the key lists (BQ-019 "acknowledge and record", BQ-026 "interest keeps accruing",
  BQ-005's second citation). The answers are shorter than the key, not wrong.
- BQ-021-style call summaries can name an employer; masking it would need the employer from the customer record, as
  the name masking does (`ui/tiui/security.py` `mask_names`).
- BQ-014 needs a pilot-specific metric variant (assignment-time certification on randomised hardship cases).

## Follow-up (4 Oct, later): every review answer re-read, BQ-017 reproduced, BQ-014 answered, policy answers complete

Re-run of all 35 on the published warehouse, each answer checked again against the documents, the transcripts and SQL
on the raw release files.

**The six "review" dev answers are correct.** BQ-002, BQ-016 and BQ-022 were complete. BQ-005, BQ-019 and BQ-026 were
correct but partial; fixed below.

**BQ-017 stays as is.** The key's reference SQL, run on the raw snapshot files, reproduces the key exactly (auto_loan
7.44% on 430, card 2,699, ...). The same SQL with `%m/%d/%Y` added gives our numbers exactly (auto_loan 8.15% on 466,
card 2,892, ...). 418,319 of 12,569,692 rows are month-first and the key drops them. Auto_loan differs by 0.71 points
against a ±0.5 tolerance, so the scorer fails it; matching would mean dropping those rows on purpose.

**Test questions recomputed from the raw files** (not only read): BQ-008 29.46% (holds left out, as the metric
definition says; 28.51% with them), BQ-013, BQ-015 (every count and amount to the cent), BQ-025, BQ-027 and BQ-030
(14,628 to 6,170 outbound calls) all match. BQ-001, 009, 020, 021, 023, 029 and 033 read correctly against their sources.

| Q | Before | After |
|---|---|---|
| BQ-014 | "A certification-pilot comparison needs a reviewed variant": no answer, though the tolerance is "direction of effect" | Certified 4.06% (48 of 1,183) vs not certified 2.45% (33 of 1,349), +1.61 points, z = 2.30, p = 0.022: **higher**. `gold.semantic_assignments` (template-only) + `cure_rate` variant `certification_pilot`; unit = one `randomised_pilot` assignment of a case flagged hardship at assignment, certification as recorded at assignment, cured within 30 days after assignment, matured, holds out. Higher under all five definitions tried (all assignments, first per case, case hardship flag, with or without holds) |
| BQ-019 | "Offer at least one eligible hardship option before asking for payment" | "Acknowledge the hardship, record the hardship_flag with the stated reason, and offer at least one eligible option ... before asking for payment. Not request a payment the customer has said they cannot afford." (all three steps of POL-COLL-004 §4.2; the last added from the `conditions` field, in the policy's own words) |
| BQ-026 | "3 months, 50%" | "... 3 months ... 50% of the regular payment. Interest keeps accruing. The missed amount is added to the end of the term." |
| BQ-005 | cites FAQ Q2 only | cites FAQ Q2 and POL-COLL-001 §5.3 |

Policy prompt `rag_answer_v2` (`ask/rag/answer.py`): complete before short, every source that states the answer, and a
`conditions` list. The model would quote a condition and still leave it out of its answer (BQ-026, with three prompt
wordings tried); listed apart, it names them, and `grounded_conditions` adds each one only when 80% of its words are in
the verified quotes and the answer does not already say it.

Dev score unchanged (14 pass, 6 review, 1 fail: BQ-017). Unseen end-to-end set 26/26, uncached and cached, on the paid
`nvidia/nemotron-3-super-120b-a12b` (`eval/e2e_unseen_report.md`). pytest 1,317 passed, 22 skipped.
