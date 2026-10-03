# P2 refusal-context fixes — 3 October 2026

Built on Claude's `902a036` and malformed-model-field fix `3938e69`.
Ownership remains with Codex for the refusal gate, injection screen and refusal
lexicon. The metric router, catalogue, model prompt and evaluation files were
left unchanged. No push was made.

## Fresh evaluation interpretation

Claude's once-scored result is **33/39 = 84.6%**, with metric cases **16/20**.
This is the pre-fix unseen result. The six misses comprise two missed security
refusals, two false refusals and two metric-selection/interpretation misses.
They are not all failures of the refusal gate. Passing the policy, SQL and
driver examples is useful evidence on this small set, not proof that those
paths are universally correct.

The fresh set was not rescored during this fix pass. Its sentences were not
copied into rules, profiles or new tests. The two metric misses were not tuned.

## Changed classes

1. **Personal versus operational age.** The bare `age` and unqualified age-band
   bans were replaced with references to customer/borrower age, personal age
   groups/distributions and people with numerical age qualifiers. Account,
   case, debt, arrears and loan age remain valid operational terms. Local
   personal-age checks still reject mixed questions asking for both account
   age and borrower age; an operational mention does not exempt the question.
2. **Contact details versus communication channels.** PII rules now require
   disclosure intent and actual detail terms instead of bare `phone`.
   The intent requirement applies equally to handwritten regexes and generated
   sensitivity-registry phrases. Bare generated `email`/`phone` phrases are
   inspected per mention to distinguish channel use and operational statistics
   from details. A mixed statistics-and-address-export request remains refused.
   Procedure questions about checking details do not imply disclosure.
3. **Names and inferred personal origin.** A new `name_origin_discrimination`
   category combines origin/name signals with targeting, prioritisation,
   avoidance or exclusion intent. It handles both name-before-origin and
   origin-before-name wording. Benign procedural discussion and operational
   uses of the word origin are not blanket banned.
4. **Instruction overrides.** Generic forget/ignore/disregard/bypass/override
   directives targeting assistant rules, restrictions or instructions are
   screened, including direct imperative forms. Discussion of agents' ordinary
   collection rules and customers forgetting payments remains allowed.

These remain heuristic language screens. Existing server-side access checks,
PII column restrictions and SQL validation remain enforcement controls.

## Verification

`tests/test_refusal_context.py` adds **79 independent synthetic checks** for
these classes, allowed counterparts, mixed questions and public service gate
ordering. No refused request in its service tests can reach the model or SQL.

| Check | Result |
|---|---|
| Full pytest before fixes | **376 passed** |
| Full pytest after fixes | **455 passed**, 22.61 seconds |
| Development router with `--llm` | **72/72 = 100%**, unchanged |
| Development router without model | **55/72 = 76.4%**, unchanged |
| False refusals on development legitimate cases | **0/56** |
| All 35 released path/metric-ID classifications | Unchanged |
| Five released refusal IDs | BQ-018/023/024/029/031, unchanged |
| Dev benchmark with `--llm` | **14 pass / 6 review / 1 fail**, unchanged BQ-017 |
| Fresh 39-case set after edits | **Not rescored**; no new unseen accuracy claim |

Validation used the existing virtual environment with `PYTHONPATH=src`,
`PYTHONDONTWRITEBYTECODE=1`, and `HF_HUB_OFFLINE=1` for local embedding loading.
The benchmark used the existing model provider/cache and the development
database `warehouse/ask_dev/trustissues_ask_dev.duckdb`; output was written to
`/tmp/p2_refusal_context_llm.csv`. No curation or publication was initiated.
The unrelated `scripts/curate_parallel.py` was left untouched.

## Handoff

Freeze this implementation before Claude writes/scores the planned independent
approximately 20-case refusal/false-refusal confirmation set. Count security
misses and legitimate questions wrongly refused separately. Decide completion
using that confirmation result; this fix pass does not mark the router done.
Keep the push held until review and confirmation are complete.
