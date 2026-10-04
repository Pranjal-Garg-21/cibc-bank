# P2 unseen end-to-end evaluation

26 frozen new questions; as-of 2026-09-28; real published warehouse opened read-only. First pass disables the LLM cache; the second replays successful first-pass replies from an isolated cache. Initial references were authored and run before Ask; driver headline totals were additionally audited after the runs; numeric tolerance is 0.5%. No application tuning.

| Shape | Uncached pass/fail | Cached pass/fail |
|---|---:|---:|
| metric | 7/1 | 7/1 |
| drivers | 1/1 | 1/1 |
| sql | 2/2 | 2/2 |
| policy | 2/1 | 2/1 |
| customer | 3/0 | 3/0 |
| refusal | 4/0 | 4/0 |
| unsupported | 2/0 | 2/0 |

Times: uncached 33.8s; cached 2.3s; total including checks 38.8s. Model attempts: 39 / 0; logical calls: 39 / 39; cache hits on replay: 39. Rate-limit responses: 0.
Numeric cache mismatches: none. Protected-file changes: none.

Final automated result: **21/26 pass** in each run. The initial segment-only check scored 22/26; the subsequent headline audit identified D02. Manual policy review identifies P01 as a checker false positive, leaving **four system failures**. The frozen question/check CSV was not altered.

Failures and manual review:
- **M05** “For April 2026, average the outbound touches per open case by current bucket and queue, keeping untouched cases in the base.” — Returned: “The requested breakdown 'by keeping' is not approved for contact attempts per case.” The axis parser expands the comma followed by keeping into a new by keeping grouping, rejecting a supported quantity and axes.
- **S02** “For each queue, how many distinct cases opened in February 2026 also logged at least one promise created in March 2026?” — Returned: “The requested breakdown 'for each queue' is not approved for promise kept rate.” The question asks for a two-table distinct-case count, but routes toward promise kept rate and never executes generated SQL.
- **S03** “At 28 September 2026, what fraction of open cases have never had a promise-to-pay record? Give the zero-promise case count, all-open case count, and percentage.” — Returned: “Which metric do you mean: Payroll delay share or Average days past due?” The zero-promise ratio falls into a clarification between unrelated payroll-delay and average-DPD metrics; generated SQL is not reached.
- **D02** “Why did outbound call volume change between 4-10 May 2026 and 11-17 May 2026? Attribute the movement to operating segments rather than guessing an incident.” — Headline reports 4,659 → 5,289 (+630); independent SQL gives **4,699 → 5,326 (+627)**. The before/after errors exceed 0.5%. The driver drops NULL province segments and uses the last dimension’s totals; exactly 40 and 37 calls have NULL province.
- **P01** “A borrower has put a cease-communication request in writing; under policy, do mandatory statements still go out and which communication channels stop?” — Returned: “Mandatory statements continue; calls, texts, and emails stop.” The question asks about mandatory statements and stopped channels. The answer correctly says statements continue and calls/texts/emails stop. The frozen keyword check additionally demanded letter, which the question did not ask.

Four system failures and one checker false positive. The rate-driver headline and returned segment numbers match references; the call-volume headline fails. No PII pattern/name hits, false refusals, or data rows on refused cards were found. Questions and frozen checks were unchanged; the new tester now also checks headline totals, without another Ask/model run.

Limits: this is a small single-machine sample; regex/exact-name PII scans and keyword policy checks are not exhaustive. Cache replay tests reproducibility, not independent model generations. Driver references check segment quantities/ranking, not causal truth; prose checks do not establish complete semantic correctness.
