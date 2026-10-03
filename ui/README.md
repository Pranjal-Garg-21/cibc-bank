# TrustIssues web UI

A formal, dark-themed web app for the collections system: **Ask**, **My queue**, **Decision card**,
**Team & approvals**, **Identity review** and **Data status**. Design: [`docs/ui_design.md`](../docs/ui_design.md).

```
ui/
  serve.py        start the server (API + built app), 127.0.0.1 only
  tiui/           FastAPI adapter over trustissues.* (package named tiui so it never clashes with app/ui.py)
  web/            React + TypeScript (Vite, Tailwind)
tests_ui/         API, security and integration tests
```

## Run

```
pip install -r ui/requirements.txt          # fastapi, uvicorn (the backend's own deps are already installed)
cd ui/web && npm ci && npm run build && cd ../..
python ui/serve.py --demo                    # http://127.0.0.1:8000
```

`--demo` turns on the demo staff picker (no real authentication; a `DEMO` tag is always shown). Without it nobody
can sign in, which is the safe default. Other switches: `--dev` (accepts the Vite dev server and serves `/api/docs`),
`--ask-llm` (lets Ask use the language model and policy search), `--port`.

Frontend development with hot reload: run `python ui/serve.py --demo --dev`, then `cd ui/web && npm run dev` and
open http://localhost:5173.

The server reads the warehouse from the usual place (`config/settings.yaml`, or `WAREHOUSE_DIR`). For the demo, keep
the warehouse outside OneDrive or pause syncing.

## What works on which branch

Every backend module is imported lazily. A screen whose module is not merged yet shows "not connected" instead of
failing.

| Screen | Needs | On `main` today |
|---|---|---|
| Identity review | `factory.identity` (P1), `gold.identity_review_queue` | works |
| Data status | `gold._publish_log`, `gold._contract_results`, `reports/` | works |
| Ask | `trustissues.ask` (P2) | not connected |
| My queue, Decision card, Team & approvals | `trustissues.nba`, `trustissues.features` (P3) | not connected |

## Rules this folder follows (design §2)

- Nothing outside `ui/`, `tests_ui/` and `docs/ui_design.md` is edited by UI work.
- No long-lived warehouse connection: each request opens and closes one, so `make nightly` is never locked out by an
  idle UI. If the nightly job holds the lock, the UI says "Data is being refreshed" (HTTP 503).
- Writes go only through backend functions (`nba.review.record`, `approve_batch`) or the identity decisions CSV that
  `factory/identity/review_queue.py` reads. A decision there takes effect on the next identity run.
- The UI's own access log and LLM audit go to `warehouse/ui_audit.duckdb`, never the warehouse.

## Security in short (design §9)

Server-side sessions in an `HttpOnly; SameSite=Strict` cookie; 15-minute idle and 8-hour absolute timeouts; CSRF
token and same-origin check on every write; deny-by-default role checks on the server, re-checked against
`curated.agents` on every request; strict Content Security Policy with no inline scripts and no third-party hosts;
`Cache-Control: no-store` on all API responses; PII masked on the server by role, with phones shown only as their
last four digits; nothing kept in browser storage; rate limits on sign-in, Ask and writes; CSV exports protected
against formula injection; errors carry a request id, never a stack trace.

## Tests

```
python -m pytest tests_ui -q                 # API and security tests on a tiny temporary warehouse
cd ui/web && npm test                        # formatting and CSV tests
TI_UI_INTEGRATION=1 python -m pytest tests_ui/test_integration.py -q   # real P2/P3 modules + real warehouse
```

The integration tests skip until P2's and P3's modules are importable.
