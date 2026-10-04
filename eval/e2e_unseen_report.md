# P2 unseen end-to-end evaluation

26 frozen new questions; as-of 2026-09-28; real published warehouse opened read-only. First pass disables the LLM cache; the second replays successful first-pass replies from an isolated cache. References were authored and run before Ask; numeric tolerance is 0.5%. No application tuning.

| Shape | Uncached pass/fail | Cached pass/fail |
|---|---:|---:|
| metric | 8/0 | 8/0 |
| drivers | 2/0 | 2/0 |
| sql | 4/0 | 4/0 |
| policy | 3/0 | 3/0 |
| customer | 3/0 | 3/0 |
| refusal | 4/0 | 4/0 |
| unsupported | 2/0 | 2/0 |

Times: uncached 55.1s; cached 2.6s; total including checks 60.4s. Model attempts: 40 / 0; logical calls: 40 / 40; cache hits on replay: 40. Rate-limit responses: 0.
Numeric cache mismatches: none. Protected-file changes: none.

Failures (observations; no fixes):

Limits: this is a small single-machine sample; regex/exact-name PII scans and keyword policy checks are not exhaustive. Cache replay tests reproducibility, not independent model generations. Driver references check segment quantities/ranking, not causal truth; prose checks do not establish complete semantic correctness.
