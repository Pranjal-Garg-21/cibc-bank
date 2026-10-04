# CIBC Collections web console (TrustIssues web UI)

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

Since 4 Oct this console is the project's app (the Streamlit `app/` was removed): `make app` builds the web app if
`ui/web/dist` is missing and starts `ui/serve.py --demo` (change with `APP_FLAGS=`, e.g. `APP_FLAGS="--demo --ask-llm"`).

```
pip install -e ".[dev,app]"                  # or: pip install -r ui/requirements.txt (fastapi, uvicorn, pyotp, ...)
cd ui/web && npm ci && npm run build && cd ../..   # or: make ui-build (Node 20+)
python ui/accounts.py add AG-1399            # a team lead account (prompts for a password)
python ui/accounts.py enroll-mfa AG-1399     # verify identity, pair authenticator, save recovery codes
python ui/serve.py --demo                    # http://127.0.0.1:8000
```

The sign-in page is one form for everyone: **staff ID and password** (plus a verification code where MFA is on). The
browser never chooses or sends a role: after the password is checked the server reads the role from
`curated.agents.role`, and the app opens that role's screens, starting on its Overview. A failed sign-in says the same
thing whether or not the staff ID exists (`docs/ui_decisions.md` D37, which replaced the four role tabs).

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
the same table but is only a convenience). Each role has its own first screen; the role comes from `curated.agents`.

| Role (`agents.role`) | Screens | Customers it may open |
|---|---|---|
| `agent`, `senior_agent` | Overview, My work queue, Next best action, Policy & case questions | Own recommendations only |
| `team_lead` | Overview, Next best action, Team & approvals, Decision review (own team), Agent accounts, Identity review, Policy & portfolio questions, Data quality | All (decides the ones assigned to them) |
| `hardship_specialist` | Overview, My work queue, Next best action, Hardship approvals, Policy & case questions | Their own recommendations, and hardship, vulnerability and support-call cases |
| `qa_analyst` | Overview, Decision review (whole bank), Identity review, Policy & portfolio questions, Data quality, Audit log | None: aggregates only |

Separation of duties: staff decide only the recommendations assigned to them (agents, hardship specialists and team
leads are all given some by the queue) and agents approve nothing; hardship specialists approve only
hardship and vulnerability cases; only team leads approve high balances and the daily batch of automated reminders
(SMS, email and app notifications, which have no agent); QA can approve or
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
recommendations, two weeks of past decisions, one agent who overrides far more than the rest). Sign in with the staff
ID and password; the role comes from the staff record:

| Staff ID | Password | Person |
|---|---|---|
| `DEMO-A1` | `Agent-Maya-2026!` | Maya Patel, agent |
| `DEMO-A2` | `Senior-Oliver-2026!` | Oliver Chen, senior agent |
| `DEMO-A3` | `Agent-Camille-2026!` | Camille Roy, agent (not vulnerable-customer certified) |
| `DEMO-L1` | `Lead-Avery-2026!` | Avery Laurent, team lead |
| `DEMO-H1` | `Hardship-Noor-2026!` | Noor Haddad, hardship specialist |
| `DEMO-Q1` | `Quality-Priya-2026!` | Priya Raman, QA analyst |

These are demo-only credentials for synthetic data; they are printed when the preview starts and open nothing else.
The preview uses stand-ins for P3's queue/review module and P2's Ask (`ui/preview_backend.py`) that follow the same
role rules, so every screen works end to end. The real server never imports them. This preview uses a private,
temporary synthetic warehouse and never opens the project's account or customer files. Closing it removes its data.
The default preview visibly disables MFA; only labelled demos accept `UI_REQUIRE_MFA=0`. The regular server keeps
MFA enabled unless explicitly configured otherwise.

`--demo` marks a synthetic-data demo (it allows `UI_REQUIRE_MFA=0`; the header no longer shows a DEMO ENVIRONMENT tag,
and the MFA line reads "Demo access · MFA enabled in deployment"). Other switches: `--dev` (accepts the Vite dev server and serves
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

Every backend module is imported lazily. A screen whose module is missing shows "not connected" instead of
failing. Since the 4 Oct integration (branch `integrate/ui-web`, `docs/ui_decisions.md` §11) every screen is connected:

| Screen | Needs | On `main` |
|---|---|---|
| Agent accounts (team leads) | `curated.agents` | works |
| Identity review (team leads) | `factory.identity` (P1), `gold.identity_review_queue` | works |
| Data status | `gold._publish_log`, `gold._contract_results`, `reports/` | works |
| Ask | `trustissues.ask` (P2), P4's RAG; the model only with `--ask-llm` | works: metrics and refusals always; policy, case-call and generated-SQL answers with `--ask-llm` and a key |
| My queue, Decision card, Team & approvals | `trustissues.nba`, `trustissues.features` (P3), `gold.nba_decisions` | works (`make queue` first) |

Ask is wired like the benchmark: case questions go through `case_customer_resolver` (the case's assigned agent, or
today's queue agent, and their team lead may ask) and P4's handler reads the case's notes and calls through the
request's own connection. `GET /api/ask/examples` gives each role questions it can ask; an agent's first example is
about a case in their queue that has a recorded call. Answer text is masked like the table: identifiers are
scrubbed and the case customer's name is masked per role (`docs/ui_decisions.md` D35).

## Rules this folder follows (design §2)

- Nothing outside `ui/`, `tests_ui/` and `docs/ui_*` is edited by UI work (the 4 Oct integration also changed
  `Makefile`, `pyproject.toml` and removed `app/`).
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

`make test` runs `tests/` and `tests_ui/` together. The integration tests (7) run only with `TI_UI_INTEGRATION=1` and a
built warehouse (`RUN_LLM_TESTS=1` adds one that calls the model: a real case answer carries no phone, email,
card number, date of birth or the customer's name), in a process of their own (some `tests/` keep a read-only connection to the warehouse, and DuckDB then
refuses the write connection the review test needs; the tests skip and say so); they sign in as real staff of each role and check the queue, card, review, Ask (including a case-call
question reaching the customer path) and identity screens.

## Adding shadcn components

`cd ui/web && npx shadcn@latest add <component>`. The CLI is run on demand and is not an app dependency (its own
dependencies carry audit findings); its stylesheet is vendored in `web/src/styles/shadcn.css`. New components import
`cn` from the `cn` package (shadcn's own). Check `npm audit` stays clean afterwards.
