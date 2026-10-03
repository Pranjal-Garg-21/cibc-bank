# TrustIssues web UI

A web console for the collections system, styled with CIBC's brand colours and built with shadcn/ui: **Ask**, **My queue**, **Decision card**,
**Team & approvals**, **Identity review** and **Data status**. Design: [`docs/ui_design.md`](../docs/ui_design.md).

```
ui/
  serve.py        start the server (API + built app), 127.0.0.1 only
  tiui/           FastAPI adapter over trustissues.* (package named tiui so it never clashes with app/ui.py)
  web/            React + TypeScript (Vite, Tailwind, shadcn/ui; components in web/src/components/ui)
tests_ui/         API, security and integration tests
```

## Run

```
pip install -r ui/requirements.txt          # fastapi, uvicorn (the backend's own deps are already installed)
cd ui/web && npm ci && npm run build && cd ../..
python ui/accounts.py add AG-1304            # create a sign-in account (prompts for a password)
python ui/serve.py --demo                    # http://127.0.0.1:8000
```

Staff sign in with their staff ID and password. Only an administrator can create accounts, and only for active
staff: `python ui/accounts.py add <staff id>` (add `--generate` to create a random password, printed once),
`remove <staff id>`, `list`. Passwords are stored as scrypt hashes in `warehouse/ui_accounts.json`, never in git.
The sign-in page shows nothing about any member of staff.

`--demo` shows the DEMO ENVIRONMENT tag. Other switches: `--dev` (accepts the Vite dev server and serves
`/api/docs`), `--ask-llm` (lets Ask use the language model and policy search), `--port`, `--accounts-file`.

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

Staff ID + password sign-in (scrypt hashes, account lockout, no staff directory); server-side sessions in an `HttpOnly; SameSite=Strict` cookie; 15-minute idle and 8-hour absolute timeouts; CSRF
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

## Adding shadcn components

`cd ui/web && npx shadcn@latest add <component>`. The CLI is run on demand and is not an app dependency (its own
dependencies carry audit findings); its stylesheet is vendored in `web/src/styles/shadcn.css`. New components import
`cn` from the `cn` package (shadcn's own). Check `npm audit` stays clean afterwards.
