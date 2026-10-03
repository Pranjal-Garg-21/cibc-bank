# TrustIssues: Recommended Tech Stack

**Status: proposal, 2026-10-03 evening IST. Not yet agreed by the team.** Written from scratch, without assuming the
earlier Streamlit choice. If it is adopted, update §1 of [`implementation_plan.md`](implementation_plan.md) and
delete this note or mark it as decided.

**In one line:** keep the data and model core (it is already the right choice), replace Streamlit with FastAPI and
React, and move everything people write (overrides, approvals, audit) out of the warehouse into its own small database.

---

## 1. Stack

| Concern | Choice | Status |
|---|---|---|
| Engine | DuckDB over Parquet, one machine | Keep. 1M customers fits easily; Spark or Polars would add nothing |
| Identity | SQL rules → Splink → LLM second opinion → human review | Keep |
| Data quality | Our own checks, driven by YAML and the data contract (`config/dq_checks.yaml`, `contracts/`) | Keep. **Drop Great Expectations**: the design names it, but we never used it. Say so under "what changed" in the README |
| Feature store | Feast, offline store only, over the same `features/sql/*.sql` files | Keep. Direct proof of "one definition for training and scoring". No Redis |
| Model | LightGBM + SHAP, with rules applied first | Keep |
| Ask (questions) | sqlglot checks + certified metric templates (P2's branch) | Keep |
| Policy search | BM25 (already on P4's branch) + LLM | **Drop Chroma.** The policy set is small. If keyword search misses things, store embeddings in DuckDB rather than run another service |
| LLM | One wrapper, `common/llm.py`, which already supports OpenRouter and Anthropic | Two tiers: a strong model for live answers and SQL (e.g. Claude Sonnet 5), a cheap one for bulk work on notes, transcripts and identity pairs (e.g. Claude Haiku 4.5). Swapping models is a config change |
| **API** | **FastAPI**, one process wrapping the existing functions: `/ask`, `/queue`, `/cases/{id}`, `/review`, `/identity-review` | **New.** The benchmark runner and the UI call the same `ask()`, which matches the "run your system as it is" rule |
| **UI** | **React + TypeScript (Vite)**, Tailwind + shadcn/ui, TanStack Table/Query, Recharts. Built to static files that FastAPI serves, so `make app` stays one command | **Replaces Streamlit** |
| **Writes by people and the app** | **SQLite (WAL mode)** for overrides, approvals, identity decisions and the audit log. The app opens the warehouse read-only; DuckDB can attach the SQLite file, so SQL joins still work | **New** |
| Scheduling | Makefile + `scripts/nightly.py` | Keep. Put Airflow or Dagster on the architecture slide as the production version only |

---

## 2. Why the three changes

### React over Streamlit

- Streamlit reruns the whole script on every click. That makes approving or overriding single rows in the queue,
  editing Ask's filters, and switching between role views clumsy.
- Live answers can stream word by word, which helps when judges ask unseen questions on the spot.
- Bank reviewers see a product rather than a notebook. Presentation and the working demo are 50% of the score.
- Finalists get several days to refine, and this is a base worth refining.
- Vite over Next.js: nothing needs server rendering or hosting.

### SQLite for writes

This fixes a real conflict. DuckDB allows only one process to write to a file. Today `nba/review.py` inserts into
`gold.nba_overrides` and P2's `common/audit.py` inserts into `gold.audit_log`, both inside the warehouse. A running app
holding a write connection would block the nightly rebuild, and a rebuild would lock out the app. P1's status note
already warns about this ("one writer per DuckDB file", [`team_status.md`](team_status.md) §6).

### Dropping Great Expectations and Chroma

Neither is in use, and both are extra services to run without adding much to the demo.

---

## 3. Risk and fallback

About 24 hours are left before the 4 Oct 21:00 IST deadline, and React is the only part that could slip. To contain it:

- Build just four screens: **Ask**, **Agent Queue**, **Decision Card**, and **Review** (team-lead approvals plus
  identity review).
- Keep the Streamlit pages working until React matches them, with a hard cutoff around **15:00 on 4 Oct**; demo
  whichever version is better then.
- The FastAPI and SQLite changes are worth doing either way.
- If nobody on the team can put a day into React, stay on Streamlit and make only the SQLite change.

---

## 4. If adopted: first steps

1. Update §1 of [`implementation_plan.md`](implementation_plan.md) (stack table) and the README's "what changed" section.
2. Move P3 writes (`nba/review.py` → `gold.nba_overrides`) and the audit log to SQLite; the app opens the warehouse
   read-only.
3. Stand up FastAPI over the existing functions; point `scripts/run_benchmark.py` at the same `ask()`.
4. Build the four React screens; keep Streamlit until the cutoff.
