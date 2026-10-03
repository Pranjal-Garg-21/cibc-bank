# P2 local router trial — 3 October 2026

This trial builds on Claude's committed service `9126841`. The user authorised the catalogue/router/refusal/injection files and lexicon for the trial, then authorised two minimal hooks: top-two clarification display in `service.py` and extra graph text in the fallback prompt. No metric SQL, parameter extraction, access policy, validator rules, or semantic join allow-list was changed. Claude's design/README files and the curation worker were left alone.

## Result

| Acceptance check | Before | After |
|---|---|---|
| Router evaluation | 34/72 = 47.2% | **55/72 = 76.4%** |
| Metric paraphrases | 9/35 | **18/35** |
| Policy / customer / driver / SQL evaluation cases | 4/8, 3/3, 2/4, 2/3 | **8/8, 3/3, 4/4, 3/3** |
| Refusal/injection evaluation cases | 11/16 | **16/16** |
| False refusals on legitimate evaluation cases | — | **0/56** |
| Five released benchmark refusals | 5/5 | **5/5**, exactly the same IDs |
| All 35 released classifications (path and metric ID) | Recorded before editing | **All unchanged** |
| Full pytest suite | 281 passed before Claude's subsequent service commit | **306 passed in 22.81 seconds** |
| Dev score without model | 13 pass, 2 fail, 6 review | **Unchanged**; failures BQ-017 and BQ-035 |
| Dev score with `--llm` | 14 pass, 1 fail, 6 review | **Unchanged**; failure BQ-017 |

The evaluation file was not edited. Its SHA-256 for this 72-case snapshot is `98b19e479dddaecc3a0ec5c1f70d693a86350342a1f60d1639d127e0ca063f49`. The evaluation is a development set, not a production or held-out accuracy claim. All 17 remaining metric misses return clarification rather than selecting the wrong metric on this set. New held-out paraphrases remain essential.

## Implemented behavior

1. **Local semantic matching:** FastEmbed/ONNX `BAAI/bge-small-en-v1.5` runs on CPU. Profiles are copied from released metric metadata (name, definition, synonyms, original catalogue example), plus the two reviewed dispute/calling-hours template contracts. No evaluation examples were added to the profiles. Strong deterministic triggers remain first; the model helps with remaining analytical questions. Policy and entity-count intent checks avoid mapping those questions to a nearby rate.
2. **Conservative selection:** a metric must be in `METRICS`, cosine similarity must be at least 0.65, and its lead over the next metric must be at least 0.055. These are similarity/margin thresholds, not calibrated probabilities. Otherwise Ask returns `cannot_answer` with the top two metric names and a question asking which is meant. Neither selection nor clarification changes formulas, parameters or permissions.
3. **Generated refusal phrases:** sensitive field names/readable variants come from `config/sensitivity.yaml`. Protected and proxy phrases are indexed; direct-PII phrases additionally require disclosure intent. Precise field phrases are retained instead of banning every component word from a field name. Existing context-sensitive regex rules remain in place. General out-of-scope and explicit instruction-override patterns were expanded.
4. **Aho-Corasick:** generated literals are searched by a cached trie-based automaton with whole-word boundaries and existing category precedence. This is Aho-Corasick's trie, not a separately implemented compressed Radix Trie. Without the optional accelerator, boundary-aware literal regex matching preserves refusal enforcement.
5. **Graph hints:** a semantic serving graph uses only existing `SEMANTIC_JOIN_KEYS`. Mentioned tables get an approximate Steiner join-path hint, or an explicit no-approved-path message. The prompt also warns about fan-out and snapshot pairing. The hints add schema text only; the validator remains the enforcement point and no dictionary proposal becomes an approved join.

The trained DeBERTa injection detector was not added. Regex screening remains a heuristic layer alongside the existing access/SQL controls.

## Setup and reproducibility

Shared `pyproject.toml` and the main requirements file remain Claude's files. The extra dependencies are declared in **`requirements-router.txt`**, with the tested direct versions `fastembed==0.8.1` and `pyahocorasick==2.3.1`.

```bash
uv pip install --python .venv/bin/python -r requirements-router.txt
PYTHONPATH=src .venv/bin/python scripts/setup_router.py
```

Setup explicitly downloads public model files to the gitignored `warehouse/cache/router_embeddings`; no question/data rows are sent to an embedding API. Request-time loading uses `local_files_only=True` and `CPUExecutionProvider`. The installed model came from `Qdrant/bge-small-en-v1.5-onnx-Q`. Bootstrap currently uses the provider repository's default model revision; freeze the revision before treating scores as a reproducible production release.

A base installation without FastEmbed/model files keeps the deterministic routing path. It will not reproduce the improved semantic score until these setup steps are run. Missing optional Aho-Corasick does not remove the generated refusal checks. Package metadata consolidation should go through Claude after this trial.

## Verification commands

Commands below ran from the repo root with the existing virtual environment. Offline mode applies to Hugging Face model loading, not the already-configured answer-model provider used by `--llm`.

```bash
HF_HUB_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B scripts/eval_router.py --min 0.76
HF_HUB_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B -m pytest -q -p no:cacheprovider

PYTHONPATH=src .venv/bin/python scripts/run_benchmark.py \
  --database warehouse/ask_dev/trustissues_ask_dev.duckdb --out /tmp/p2_router_trial.csv
PYTHONPATH=src .venv/bin/python scripts/score_dev.py /tmp/p2_router_trial.csv

PYTHONPATH=src .venv/bin/python scripts/run_benchmark.py \
  --database warehouse/ask_dev/trustissues_ask_dev.duckdb --out /tmp/p2_router_trial_llm.csv --llm
PYTHONPATH=src .venv/bin/python scripts/score_dev.py /tmp/p2_router_trial_llm.csv
```

The new tests cover confidence/margin rejection, catalogue membership, service clarification without execution, agent access after semantic matching, intent precedence, offline/CPU loading, generated registry fields, literal boundaries, dependency fallbacks, and the graph prompt hook without policy changes. Existing tests cover the released route/refusal invariants. Benchmark submissions were written outside the checkout. No curation or production publication was run.

## Next evaluation

Keep the conservative thresholds for the held-out check. Report metric-selection precision separately from coverage/clarifications, and retain per-metric results. The largest gap is promise/contact paraphrases that produce close candidates. Improve catalogue descriptions using independently reviewed business terminology, then assess on fresh labels rather than tuning to these 72 questions. Actual financial-answer correctness still needs parameter/cohort/SQL evaluation beyond routing.
