# P2 router review and separate quantity-shape fixes

Reviewed `c59b248` and `f858433` on `p2-branch-ask-layer`, 3 October 2026.
The review and baseline runs happened before edits. Claude's `llm_router.py`,
`service.py`, `scripts/eval_router.py`, and both evaluation CSVs were left
unchanged. The separate fix pass changes only routing shape guards and adds
synthetic tests. No push was made.

## Read-only review findings

**Medium: malformed model JSON can crash routing.** In
`llm_router.py:55`, `metric in _metrics()` requires a hashable value. A valid
JSON response with `{"path":"metric","metric_id":[]}` or an object-valued
ID raises an uncaught `TypeError`. Unknown string IDs are rejected, but
type-invalid IDs do not safely abstain. This happens before `ask()`'s access
exception handler. Validate the result fields as strings/null before membership
tests. Left for Claude because this file was requested for read-only review.

**Medium: some catalogue descriptions exceed the certified template's scope.**
The router prompt uses the existing profile definition of `collected_amount`
as payments received on cases; the template sums `amount_paid_against` on
promises. The held-out generic recovery questions can therefore receive the
right family label while asking for a broader population. This pre-exists
Claude's router change. Profile/template alignment and requested-population
rejection need a separate catalogue review.

| Requested check | Finding |
|---|---|
| Injection/refusal precedence | Both `classify()` and `ask()` screen before `_route()`. Inputs flagged by either gate never call the model. This verifies ordering, not completeness of the heuristic screens. |
| Metric allow-list | The profile/trigger intersection currently equals all **17** approved `METRICS`. Unknown string IDs are discarded. There is no eighteenth certified contact-volume metric. |
| Path allow-list | Only `metric`, `rag_corpus` and `sql` become model routes. `cannot_answer`, unknown paths, `drivers`, `incident` and `rag_customer` return `None`, preserving the original route. |
| Prompt data | System text is routing instructions plus metric names/definitions; user text is the question. No database connection, rows, retrieved case context or SQL result is passed. |
| Metric execution | `_metric()` still checks the role, calls `build_call()`, rejects `Unsupported`, renders a fixed template, validates and executes. The model supplies no parameters, SQL or role. |
| Clarification | `_route()` intentionally lets the model arbitrate `cannot_answer`, including a top-two clarification. `None`, unknown IDs, or model `cannot_answer` preserve that exact original clarification. A valid choice may be any allowed metric, not just the original two. This is the newly authorised behavior; model certainty itself is not calibrated. |

The new tests exercise gate ordering in both public entry points, model
abstention preserving the same clarification, forbidden paths, prompt inputs,
agent access after model selection, and existing missing-period/unapproved-
breakdown rejection. They do not claim `build_call()` already rejects every
unsupported semantic variant: several label examples below show remaining
parameter/population gaps.

## Reproduced baseline at f858433

| Check | Result |
|---|---|
| `scripts/eval_router.py --heldout --llm` | **44/55 = 80.0%** |
| `scripts/eval_router.py --llm` | **72/72 = 100.0%** |
| Full pytest | **310 passed**, 22.78 seconds |
| Dev benchmark with `--llm` | **14 pass / 6 review / 1 fail**, BQ-017 |

The model uses the project's existing provider and response cache. These are
router classifications, not end-to-end correctness measurements. The dev
scorer remains an approximation rather than official grading.

## Label review

Line numbers below refer to the unchanged CSV, including its header.

| Lines | Assessment |
|---|---|
| 32–33 | **Change to `sql` with no metric ID.** Plain contact counts have no certified `contact_volume` template. `drivers/contact_volume` at line 37 is valid because decomposition has its own volume query. |
| 21 | **Recommend `sql`, subject to label-owner agreement.** Counting disputed cases in an opening cohort differs from counting dispute-flagged contact events by contact timestamp. Do not force this into `dispute_count`. |
| 20 | `dispute_count` is a reasonable family label, but the current template requires two periods; a one-period request must clarify. |
| 7 | RPC is the relevant family, but a count of successful attempts is a requested numerator, not automatically the RPC percentage. Routing alone does not score that distinction. |
| 15 | Write-off is the relevant family, but percentage of balances differs from percentage of accounts. Current count/amount outputs do not implement a balance-based rate. |
| 24–25 | General money recovered/collected is broader than the promise-linked collected-amount variant. Clarify the population or use an independently validated SQL path. |
| 26 | Delinquency family is reasonable, but 30+ among delinquent accounts differs from the template's 1+ among active/blocked accounts. Historical requested date also needs support. |
| 27 | Delinquency family is reasonable, but its month-end request is not evidence that historical or future state is supported. Current parameter extraction uses the supplied as-of date. |
| 28–29 | Cure family is plausible, but historical account cohorts and an arbitrary starting DPD range differ from the supported case-based cure variants. A family hit must not certify those populations. |
| 22–23, 31 | The metric families are plausible; contacts versus outbound calls and requested direction filters still require explicit scope checks. |
| 53 | Geographic avoidance/treatment is a plausible proxy-geography refusal. Existing phrases miss this wording; returning `cannot_answer` currently prevents execution. |
| 54–55 | Unrelated requests can reasonably be `refused` under the out-of-scope contract, or `cannot_answer` under the model prompt. Agree one convention before the fresh final evaluation. |
| Other rows | No clear family/path label error found. Some dimensions are deliberately unsupported, so a correct router classification can still produce `cannot_answer` downstream. |

The CSVs were not relabelled to improve the score. A fresh set should label
route/metric separately from expected measure, cohort, date basis and whether
the current service can express the request. Its author should resolve these
conventions before running it.

## The eleven baseline misses

There are **eight wrong non-`cannot_answer` routes**, not eleven. The last three
misses abstain. The model only runs after `cannot_answer`, so it cannot repair
the first eight as originally routed.

| CSV line | Short description | Baseline result | Label / decision |
|---|---|---|---|
| 17 | Typical days overdue | `sql` | Average-DPD family; entity-count regex crossed over the measurement unit. |
| 21 | Disputed cases in an opening cohort | `sql` | Keep SQL; dispute-event template has a different grain/date basis. |
| 22 | Measured calling-hours noncompliance | `rag_corpus` | Out-of-hours metric; measurement intent should beat a policy topic. |
| 32 | Daily outbound volume | `metric/out_of_hours_share` | SQL population count; label also needs correction. |
| 33 | Contacts grouped by channel | `metric/cost_per_contact` | SQL population count; label also needs correction. |
| 34 | Expense per right-party contact | `metric/rpc_rate` | Cost-per-RPC; quantity/unit should beat the generic contact topic. |
| 35 | Explain movement in RPC | `metric/rpc_rate` | Drivers; causal intent should not depend on a fixed list of motion verbs. |
| 43 | Distribution of case lateness | `metric/average_dpd` | SQL distribution; grouping does not imply an average. |
| 53 | Geographic avoidance | `cannot_answer` | Proxy refusal wording gap; outside this wrong-route fix pass. |
| 54 | Unrelated recommendation | `cannot_answer` | Refusal/abstention convention requires agreement. |
| 55 | Unrelated creative request | `cannot_answer` | Refusal/abstention convention requires agreement. |

## Separate class-level fixes

`requested_shape.py` now recognises unqualified population counts,
distributions and explicit unit costs before semantic metric retrieval. It
does not infer a rate or mean solely from a matching subject. Population-count
syntax requires an entity immediately after the count phrase, so a number of
measurement units does not become a count of nearby cases. Qualified event or
transition requests remain available to catalogue interpretation. Temporal
grouping such as per day is distinct from a per-case denominator.

`router.py` now recognises measurement intent consistently for policy-topic
questions, requires a permission clause rather than the bare word `allowed`,
and routes an initial causal `why` through the existing driver/incident path.
No formulas, parameters, access rules, validators, refusal rules or local
embedding thresholds/profiles were changed.

The **65 additional tests** use synthetic operator/entity/unit combinations
and safety boundaries. No held-out sentence was added to code, phrases or
tests. Existing held-out wording in Claude's committed test was left unchanged.

| Check after fixes | Result |
|---|---|
| Original, unchanged held-out labels with `--llm` | **49/55 = 89.1%** |
| Development labels with `--llm` | **72/72 = 100.0%** |
| Development labels without model | **55/72 = 76.4%**, unchanged |
| Full pytest | **375 passed**, 23.09 seconds |
| All 35 released path/metric-ID classifications | Unchanged |
| Five released refusal IDs | Unchanged: BQ-018/023/024/029/031 |
| Dev benchmark with `--llm` | **14 pass / 6 review / 1 fail**, BQ-017 |

The six remaining raw-label misses are the three disputed metric labels at
lines 21/32/33 and the three refusal-versus-abstention cases at lines 53–55.
No adjusted accuracy is presented as a new held-out result. The 89.1% is a
diagnostic score after inspecting failures; generalisation still needs the
planned fresh approximately 30-case evaluation with frozen code.

## Commands

Run from the repo root, with `HF_HUB_OFFLINE=1`, `PYTHONDONTWRITEBYTECODE=1`
and `PYTHONPATH=src`. Offline applies to the local embedding model, not the
existing answer-model provider.

```bash
.venv/bin/python -B scripts/eval_router.py --heldout --llm
.venv/bin/python -B scripts/eval_router.py --llm
.venv/bin/python -B scripts/eval_router.py
.venv/bin/python -B -m pytest -q -p no:cacheprovider
.venv/bin/python -B scripts/run_benchmark.py \
  --database warehouse/ask_dev/trustissues_ask_dev.duckdb \
  --out /tmp/p2_router_shapes_final_llm.csv --llm
.venv/bin/python -B scripts/score_dev.py /tmp/p2_router_shapes_final_llm.csv
```

No curation or production publication was initiated. The benchmark used the
specified development database. CSV outputs and the released-route baseline
were written outside the checkout.
The unrelated untracked `scripts/curate_parallel.py` was left untouched.
