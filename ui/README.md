# TrustIssues web UI

A web console for the collections system, styled with CIBC's brand colours and built with shadcn/ui: **Ask**, **My queue**, **Decision card**,
**Team & approvals**, **Identity review** and **Data status**. Design: [`docs/ui_design.md`](../docs/ui_design.md); current decisions and their reasoning: [`docs/ui_decisions.md`](../docs/ui_decisions.md).

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

The sign-in page has four tabs: **Agent**, **Team lead**, **Hardship** and **QA**.

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

## Roles and access

Every route asks one table, `ui/tiui/rbac.py`, what the signed-in role may do (deny by default; the menu is built from
the same table but is only a convenience). Each role has its own sign-in tab and its own first screen.

| Role (`agents.role`) | Tab | Screens | Customers it may open |
|---|---|---|---|
| `agent`, `senior_agent` | Agent | Overview, My work queue, Next best action, Policy & case questions | Own recommendations only |
| `team_lead` | Team lead | Overview, Next best action, Team & approvals, Decision review (own team), Agent accounts, Identity review, Policy & portfolio questions, Data quality | All |
| `hardship_specialist` | Hardship | Overview, Next best action, Hardship approvals, Policy & case questions | Hardship, vulnerability and support-call cases only |
| `qa_analyst` | QA | Overview, Decision review (whole bank), Identity review, Policy & portfolio questions, Data quality, Audit log | None: aggregates only |

Separation of duties: agents decide only their own cases and approve nothing; hardship specialists approve only
hardship and vulnerability cases; only team leads approve high balances and the digital batch; QA can approve or
change nothing and never opens a customer's case. A case a role may not see answers 404, the same as one that does
not exist. Each overview is assembled on the server for the role, so the browser never receives figures its role may not
see. Accounts for team leads, hardship specialists and QA analysts are created by an administrator with
`ui/accounts.py add`; agents' passwords are set by their team lead in the app.

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
Demo sessions (shared access and `preview.py`) last **two hours**: idle limit 120 minutes and absolute limit two hours. Restarting the
server also ends its sessions. The browser anchors the server's remaining duration to a monotonic clock, so a
different browser date/time does not cause immediate sign-out. Individual-credential mode retains its 15-minute
idle and eight-hour defaults. `UI_SESSION_TIMEOUTS=0` remains available only in demos when explicitly requested.

For an entirely separate synthetic warehouse:

```
python ui/preview.py                         # http://127.0.0.1:8000
python ui/preview.py --mfa                   # optional authenticator flow; prints demo setup keys
```

The preview seeds one account per role, each with its own password, and a believable day of work (about 50
recommendations, two weeks of past decisions, one agent who overrides far more than the rest). Pick the matching tab
on the sign-in page:

| Tab | Staff ID | Password | Person |
|---|---|---|---|
| Agent | `DEMO-A1` | `Agent-Maya-2026!` | Maya Patel, agent |
| Agent | `DEMO-A2` | `Senior-Oliver-2026!` | Oliver Chen, senior agent |
| Agent | `DEMO-A3` | `Agent-Camille-2026!` | Camille Roy, agent (not vulnerable-customer certified) |
| Team lead | `DEMO-L1` | `Lead-Avery-2026!` | Avery Laurent |
| Hardship | `DEMO-H1` | `Hardship-Noor-2026!` | Noor Haddad, hardship specialist |
| QA | `DEMO-Q1` | `Quality-Priya-2026!` | Priya Raman, QA analyst |

These are demo-only credentials for synthetic data; they are printed when the preview starts and open nothing else.
The preview uses stand-ins for P3's queue/review module and P2's Ask (`ui/preview_backend.py`) that follow the same
role rules, so every screen works end to end. The real server never imports them. This preview uses a private,
temporary synthetic warehouse and never opens the project's account or customer files. Closing it removes its data.
The default preview visibly disables MFA; only labelled demos accept `UI_REQUIRE_MFA=0`. The regular server keeps
MFA enabled unless explicitly configured otherwise.

`--demo` shows the DEMO ENVIRONMENT tag. Other switches: `--dev` (accepts the Vite dev server and serves
`/api/docs`), `--ask-llm` (lets Ask use the language model and policy search), `--port`, `--accounts-file`.

Frontend development with hot reload: run `python ui/serve.py --demo --dev`, then `cd ui/web && npm run dev` and
open http://localhost:5173.

The server reads the warehouse from the usual place (`config/settings.yaml`, or `WAREHOUSE_DIR`). For the demo, keep
the warehouse outside OneDrive or pause syncing.

## What works on which branch

In the local demo, team leads can open **Tables** at `http://127.0.0.1:8000/tables` to browse actual `curated`
and `gold` tables. Select a table to see its rows, page through 50 at a time, or open its column names and types.
Empty database stubs retain their headers and column structure. The browser is read-only, excludes internal
and audit tables, and masks sensitive/unclassified values. Sign in as a team lead; agent accounts cannot browse
the warehouse. Direct table URLs are preserved through sign-in, for example `/tables/curated/agents`.

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
