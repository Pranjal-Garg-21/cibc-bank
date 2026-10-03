# P2 structural screening handoff — 3 October 2026

Claude's confirmation set at `4f2701b` caught only 5/11 intended refusals after
`6de4b5d`. Those misses abstained downstream, but the previous gate changes
remained too dependent on phrasing. This pass changes the matching structure,
not the metric router or model prompt. No push was made.

## Implemented structures

| Gate | Required components |
|---|---|
| Name/origin discrimination | A personal name/surname or existing origin signal **and** a targeting/default action. The name matcher no longer needs an enumerated ethnic or national adjective. |
| Instruction override | An override verb **and** an instruction/rule/limit/configuration/prompt object, including references to what the assistant was told. The components can occur in either order with intervening words. |
| Instruction disclosure | A reveal/print/show/output verb **and** configuration/prompt, explicitly assistant-owned instructions, or a new-instructions marker. Ordinary requests to show collection rules/limits remain allowed. |
| Role override | Acting without restrictions/limits or another instruction object. |
| Bulk PII export | A disclosure action **and** email/phone/address/contact details **and** bulk scope, such as every/all/cohort/list. Components can occur in either order. |

Communication words can be verbs or nouns. The name gate distinguishes a
targeting action from a detail noun such as phone numbers. Operational
product/column/metric/channel names do not become personal-name targeting;
other personal-name mentions in a mixed request are still checked.

The PII gate checks each channel mention rather than exempting the whole
question. Channel use by/via email and contact statistics remain allowed,
including intervening statistical modifiers. Explicit numbers/addresses
override that channel interpretation. A statistics-plus-detail-export request
remains refused. Existing precise sensitivity-registry identifiers and
contextual personal-age checks remain in place.

## Tests and evaluation integrity

`tests/test_structural_gates.py` adds **104 synthetic checks**, including
component permutations, object-before-action requests, mixed requests,
operational counterparts and refusal before model/data execution. It uses no
sentence from `router_confirm.csv`, `router_fresh.csv` or `router_heldout.csv`.
Those files and `scripts/eval_router.py` were unchanged and were not rescored.

| Validation | Result |
|---|---|
| Full current shared-workspace pytest | **564 passed**, 22.63 seconds |
| Development router with `--llm` | **72/72 = 100%** |
| Development router without model | **55/72 = 76.4%**, unchanged |
| Legitimate development questions wrongly refused | **0/56** |
| Released classifications | All **35** path/metric-ID pairs unchanged |
| Released refusal IDs | BQ-018/023/024/029/031, unchanged |
| Dev benchmark with `--llm` | **14 pass / 6 review / 1 fail**, unchanged BQ-017 |

The suite includes Claude's concurrent additions; 564 is the full workspace
count, not a count attributed solely to these gate changes. Claude's separate
service/SQL-fallback/benchmark work was left alone. Only Codex's gate files,
new tests and this handoff are included in this delivery.

Validation used `PYTHONPATH=src`, `PYTHONDONTWRITEBYTECODE=1` and
`HF_HUB_OFFLINE=1` for local embedding loading. The existing model provider
and cache served the authorised `--llm` checks. The benchmark used
`warehouse/ask_dev/trustissues_ask_dev.duckdb`, with submission output at
`/tmp/p2_structural_gates_final_llm.csv`. No curation/publication was initiated;
`scripts/curate_parallel.py` was left untouched.

## Freeze and final confirmation

Freeze the gate implementation before Claude writes/scores the next independent
refusal set. No new unseen accuracy is claimed by this delivery. Report missed
security refusals separately from legitimate questions wrongly refused.

These checks remain regex/token heuristics. Co-occurrence and local noun
context improve coverage but do not provide general semantic understanding of
negation, quotations, references or every paraphrase. Access checks and SQL
validation remain mandatory regardless of screening results. If the next
independent check still misses cases, carry the observed limit into Claude's
design document and move on as agreed, rather than tuning another wording
round. Keep the push held pending that report.
