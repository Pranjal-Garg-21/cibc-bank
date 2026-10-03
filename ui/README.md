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
python ui/accounts.py add AG-1399            # a team lead account (prompts for a password)
python ui/accounts.py enroll-mfa AG-1399     # verify identity, pair authenticator, save recovery codes
python ui/serve.py --demo                    # http://127.0.0.1:8000
```

The sign-in page has two roles, **Team lead** and **Agent**.

- **Team leads**: an administrator creates their account on the server: `python ui/accounts.py add <staff id>`
  (team leads only; `--generate` makes a random password, printed once; also `remove`, `list`).
- **Agents**: their team lead signs in, opens **Agent accounts**, and sets each agent's password. That password is
  fixed: the agent cannot change it, and only their team lead can reset it or remove their access.

Passwords are stored as scrypt hashes in `warehouse/ui_accounts.json`, never in git. The sign-in page shows nothing
about any member of staff.

MFA is required by default, including when `--demo` only adds the environment label. After an account has a
password, the service desk runs `python ui/accounts.py enroll-mfa <staff id>` for each agent or lead. Verify the
person through the bank's identity-proofing process first. The command confirms an authenticator code before
saving the encrypted secret and prints ten single-use recovery codes once. Re-enrolment invalidates previous
sessions and recovery codes. Password resets preserve the enrolled authenticator.

Set `UI_MFA_KEY` to a Fernet key supplied by your secret manager before provisioning and serving deployed
accounts. The local fallback creates a private `ui_accounts.mfa.key` beside the accounts file; back up the key
and accounts together outside git. Losing or replacing the key without migrating secrets prevents MFA login.

## Isolated design preview

For temporary access using the project's existing `curated.agents` in DuckDB:

```
python ui/serve.py --demo --shared-access
```

Enter the chosen shared demo password at the hidden prompt. It stays in process memory; no per-person passwords
or hashes are written, and existing account files are untouched. Active agents and senior agents use **Agent**;
active team leads use **Team lead**. The selected role must match DuckDB. Unknown IDs, inactive staff and other
roles cannot sign in. Individual password changes and revocation are disabled during shared access. Stop the
process to end this temporary mode, then restart without `--shared-access` to use individual credentials and MFA.
Shared access is restricted to loopback hosts and visibly labels MFA as disabled.
Automatic idle and absolute session expiry are off in this temporary mode; sign out when finished. Restarting
the server also ends its sessions. Set `UI_SESSION_TIMEOUTS=1` to restore the usual 15-minute idle and eight-hour
absolute limits. Individual-credential mode keeps those defaults; `UI_SESSION_TIMEOUTS=0` is allowed only in demos.

For an entirely separate synthetic warehouse:

```
python ui/preview.py                         # http://127.0.0.1:8000
python ui/preview.py --mfa                   # optional authenticator flow; prints demo setup keys
```

Choose **Team lead**, staff ID **DEMO-L1**, password **Preview-only-2026!**. Agent **DEMO-A1** has the same demo
password. This preview uses a private, temporary synthetic warehouse and never opens the project's account or
customer files. Closing it removes its data. The default preview visibly disables MFA; only labelled demos
accept `UI_REQUIRE_MFA=0`. The regular server keeps MFA enabled unless explicitly configured otherwise.

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
| Agent accounts (team leads) | `curated.agents` | works |
| Identity review (team leads) | `factory.identity` (P1), `gold.identity_review_queue` | works |
| Data status | `gold._publish_log`, `gold._contract_results`, `reports/` | works |
| Ask | `trustissues.ask` (P2) | not connected |
| My queue, Decision card, Team & approvals | `trustissues.nba`, `trustissues.features` (P3) | not connected |

## Rules this folder follows (design §2)

- Nothing outside `ui/`, `tests_ui/` and `docs/ui_*` is edited by UI work.
- No long-lived warehouse connection: each request opens and closes one, so `make nightly` is never locked out by an
  idle UI. If the nightly job holds the lock, the UI says "Data is being refreshed" (HTTP 503).
- Writes go only through backend functions (`nba.review.record`, `approve_batch`) or the identity decisions CSV that
  `factory/identity/review_queue.py` reads. A decision there takes effect on the next identity run.
- The UI's own access log and LLM audit go to `warehouse/ui_audit.duckdb`, never the warehouse.

## Security in short (design §9)

Staff ID + password + authenticator or single-use recovery code (scrypt hashes, account lockout, no staff directory);
encrypted MFA secrets and replay rejection; server-side sessions in an `HttpOnly; SameSite=Strict` cookie;
15-minute idle and 8-hour absolute timeouts, a three-session limit, device/session visibility and sign-out of other
sessions; fresh verification for password resets, access removal, reviews, approvals and exports; CSRF
token and same-origin check on every write; deny-by-default role checks on the server, re-checked against
`curated.agents` on every request; strict Content Security Policy with no inline scripts and no third-party hosts;
`Cache-Control: no-store` on all API responses; PII masked on the server by role, with phones shown only as their
last four digits; nothing kept in browser storage; rate limits on sign-in, Ask and writes; CSV exports protected
against formula injection; errors carry a request id, never a stack trace. Passive expiry polling does not prolong
sessions. Sign-out clears sensitive query data across tabs, and responses from an old session are discarded.

For a network deployment, set an explicit `UI_ALLOWED_HOSTS` list and `UI_HTTPS=1`, then configure TLS and
trusted proxy headers at the reverse proxy. These flags do not create HTTPS. Run one server process while
sessions, login lockouts and rate limits use in-memory stores. Shared stores, bank SSO, off-host monitoring,
storage protection and independent security review remain deployment work. See the
[security review and rollback record](../docs/ui_security_review.md).

## Tests

```
python -m pytest tests_ui -q                 # API and security tests on a tiny temporary warehouse
cd ui/web && npm test                        # API auth flow, formatting and CSV tests
TI_UI_INTEGRATION=1 python -m pytest tests_ui/test_integration.py -q   # real P2/P3 modules + real warehouse
```

The integration tests skip until P2's and P3's modules are importable.

## Adding shadcn components

`cd ui/web && npx shadcn@latest add <component>`. The CLI is run on demand and is not an app dependency (its own
dependencies carry audit findings); its stylesheet is vendored in `web/src/styles/shadcn.css`. New components import
`cn` from the `cn` package (shadcn's own). Check `npm audit` stays clean afterwards.
