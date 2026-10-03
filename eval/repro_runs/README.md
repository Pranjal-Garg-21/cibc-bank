Full benchmark outputs (35 released questions, dev data, `--llm`) used for the reproducibility check.

| File | Cache | Code state |
|---|---|---|
| run_a_cached.csv | cached | before the stable row ordering fix |
| run_b_nocache.csv | `--no-cache` | before the fix |
| run_c.csv, run_d.csv | `--no-cache` | after the fix (commit a824d21), run at the same time |
| run_e.csv | cached | after the fix |

Runs a and b differ from each other in 10 answers by row order only. Runs c, d and e are identical in every numeric
answer; they differ in the wording of policy text answers (BQ-001, BQ-019) and in one refusal reason (BQ-023) because
the refusal rules changed between runs. Compare with: python -c "import csv; ..." on question_id.
