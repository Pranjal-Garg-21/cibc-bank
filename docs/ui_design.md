# TrustIssues: UI Implementation Design (v0.2)

**Current decisions and the reasoning behind them are in [`ui_decisions.md`](ui_decisions.md).** Where this document disagrees
(five roles and four dashboards instead of two sign-in roles, a light theme instead of dark, role-scoped screens), that file wins.

**2026-10-04 implementation update:** the `ui/banking-polish-security` branch refines the existing CIBC
interface and extends authentication with MFA, recovery codes, fresh verification and session management.
The previous pushed baseline is `3c87423`, also recorded by tag `rollback-ui-3c87423` on GitHub.
See [current controls, verification and rollback](ui_security_review.md) for the current implementation;
this document's original v1 tables below describe the baseline. Shared demo access is an explicit temporary
mode using active DuckDB staff, with no per-person credentials written. The branch and rollback tag are pushed
to GitHub; the original `ui/react-app` branch remains at the baseline.

**Status: draft, 2026-10-03; v1 built on branch `ui/react-app` (see `ui/README.md`). Owner: Anushka (P1). Not yet reviewed by P2–P4.**
Builds on the stack proposal (FastAPI + React). Where this note and the proposal disagree, see §3 and
"Corrections to the stack proposal" at the end. v0.2 adds the security design (§9) and the formal dark theme (§8).

**In one line:** a new top-level `ui/` folder holding a thin FastAPI adapter and a React app. It only *calls* the
existing public functions in `src/trustissues/`, never edits them, and can be deleted without breaking any test,
pipeline step or the benchmark.

---

## 1. Goals and non-goals

**Goals**

1. A formal, product-grade interface for four jobs: **Ask**, **Agent Queue**, **Decision Card**, **Review**
   (team-lead approvals + identity review). A read-only **Data Status** page is optional.
2. Zero changes to backend code, config, contracts or tests. The UI is a client of the backend.
3. Run as one command, one process, one port, on Windows and macOS/Linux.
4. Be explainable to judges: every number shows where it came from, what date it is as of, and whether it is
   certified, model-generated or AI-generated.
5. Security appropriate to a bank's internal tool (§9): deny by default, PII masked by role, every access and
   decision audited, and no customer data left in the browser.
6. A formal **dark theme** as the default look (§8), with a light print stylesheet for reports.

**Non-goals (v1)**

- Real SSO. v1 signs in with staff ID + password against locally created accounts, behind a proper server-side session, so swapping in
  the bank's SSO later changes only the login route (§9.1).
- Word-by-word streaming of answers (needs backend changes, see §3.4).
- Moving writes to SQLite (a backend decision for P2/P3, see §3.2). The UI is built so it can adopt it later by
  changing one file.
- Mobile layouts beyond "does not break". Desktop at 1280–1920 px wide is the target.

---

## 2. The non-interference contract

These rules are what "does not interfere with the backend" means in practice. Reviewers should reject a UI PR that
breaks any of them.

| # | Rule |
|---|---|
| N1 | All UI code lives under `ui/`. No file under `src/`, `config/`, `contracts/`, `tests/`, `scripts/`, `feature_repo/` or `app/` is edited by UI work. |
| N2 | The API imports only **public** functions (no leading underscore) from `trustissues.*`. The list is in §5. If a function we need does not exist, we ask the owner for it; we do not write it in `src/`. |
| N3 | The API holds **no long-lived DuckDB connection**. Each request opens, uses and closes one (§3.1), so the nightly rebuild is never locked out by an idle UI. |
| N4 | The API writes only through existing write functions (`nba.review.record`, `nba.review.approve_batch`) or existing file interfaces (`data/review/identity_decisions.csv`, using `review_queue.FIELDS`). It never runs its own `INSERT`/`UPDATE` into the warehouse. |
| N5 | Every backend module is imported **lazily and optionally**. If a teammate's branch is not merged yet (e.g. `trustissues.nba`), the matching endpoints return `501 not_connected` and the screen says so. The UI branch can merge before, after or between the others. |
| N6 | The Streamlit app in `app/` keeps working, unchanged, until the team decides to retire it. |
| N7 | UI dependencies are kept out of `pyproject.toml` core deps: Python ones go in `ui/requirements.txt` (or a `ui` extra added by agreement), JS ones in `ui/web/package.json`. |

---

## 3. Architecture

```
 Browser (React SPA)
   │  fetch /api/*  (JSON)
   ▼
 ui/tiui  (FastAPI, uvicorn, one process)
   ├─ /api/*      thin adapters → trustissues.* public functions
   ├─ /           static files from ui/web/dist (built React app)
   └─ deps.py     the ONLY place that opens connections / builds LLM, audit, RAG objects
   │
   ├─ DuckDB warehouse  (warehouse/*.duckdb)   per-request connection
   ├─ data/review/identity_decisions.csv       append-only, identity review
   └─ ui audit DB  (warehouse/ui_audit.duckdb)  LLM audit rows from the UI (§3.3)
```

### 3.1 Connection policy (the important part)

DuckDB allows **either** one read-write process **or** many read-only processes on a file, never both. A UI that
keeps any connection open, even read-only, stops the nightly job from opening the warehouse for writing.
Today's Streamlit `app/ui.py` holds a read-write connection for as long as it runs, which is the conflict
`team_status.md` warns about.

Policy for the API:

- `deps.warehouse()` is a FastAPI dependency that opens `trustissues.common.db.connect()` for the length of one
  request and closes it in a `finally`. Requests are serialised with a process-wide lock (a demo has one or two
  users; this costs nothing and avoids two in-process connections with different configs).
- Per request, call `features.sources.register(con)` where the endpoint needs the P3 source views (they are TEMP
  views, so they vanish with the connection).
- When DuckDB cannot get the file lock (nightly is running), the API returns `503 {"code": "warehouse_busy"}`
  and the UI shows a calm banner: "Data is being refreshed. Try again in a minute." No retries in the browser
  beyond one after 5 s.
- When the team moves overrides and audit to SQLite (the proposal's option), only `deps.py` changes.

Spike to do first (30 min): measure open-query-close time on the full warehouse. Expect tens of milliseconds;
if it is over ~300 ms, fall back to keeping a connection open while the browser tab is active and closing it after
30 s idle.

### 3.2 Writes

| Write | How the API does it | Where it lands |
|---|---|---|
| Agent accept / override | `nba.review.record(con, rec_id, user_id, decision, reason_code)` | `gold.nba_overrides` (unchanged) |
| Approve / reject one | same `record` with `approved` / `rejected` | `gold.nba_overrides` |
| Approve digital batch | `nba.review.approve_batch(con, lead_id, as_of, rejected)` | `gold.nba_overrides` |
| Identity decision | append one row to `data/review/identity_decisions.csv` with `review_queue.FIELDS`, `evidence_key` copied from the queue row | CSV; applied on the next identity run (say so in the UI) |

`review.py` already re-checks who may decide what; the API passes `ReviewError` messages straight to the UI as a
`409` with the text shown in a form-level error.

### 3.3 LLM calls and the audit log

`common.llm.LLM(audit=...)` takes any object with `record(...)`. The API gives it
`common.audit.AuditLog(<connection to warehouse/ui_audit.duckdb>)`, a separate small file. This keeps the
governance requirement (every LLM call audited) without writing to the warehouse. The team can later merge those
rows into `gold.audit_log` or switch to SQLite; again only `deps.py` changes.

### 3.4 Ask: no streaming in v1

`ask()` returns a finished `AnswerCard`, and `common/llm.py` backends return a whole string. Streaming would need
changes to both, which is backend work. v1 instead shows **progress states**: "Checking the question → Finding the
metric / policy → Running the query → Writing the answer", driven by elapsed time, then the full card. With the
current model setting (~10 s per answer, `settings.yaml`) this is enough.

---

## 4. Repository layout

```
ui/
  README.md                 how to run, the non-interference rules (§2)
  requirements.txt          fastapi, uvicorn, httpx (tests)
  serve.py                  one-command start, 127.0.0.1 only (--demo, --dev, --ask-llm)
  tiui/                     Python package (named tiui so it never clashes with P3's app/ui.py)
    main.py                 app factory; mounts /api routers and ui/web/dist
    settings.py             UI_* environment settings with safe defaults (§10)
    deps.py                 connections, LLM, audit, RAG index, current user from the session
    security.py             sessions, CSRF, role guards, rate limits, masking fallback (§9)
    middleware.py           security headers, request ids, access log, body size limit
    errors.py               ReviewError→409, AccessDenied→403, lock→503, ImportError→501; no stack traces out
    schemas.py              Pydantic response models (mirror AnswerCard, card(), queue rows)
    routers/
      session.py            staff list, current user, as-of date
      ask.py
      queue.py
      cases.py              decision card + case context
      review.py             accept/override/approve/reject/batch
      identity.py           review queue + decisions
      status.py             pipeline / DQ report summaries (read-only)
  web/
    package.json, vite.config.ts, tailwind.config.ts, tsconfig.json
    src/
      main.tsx, router.tsx
      api/                  typed client, TanStack Query hooks, error mapping
      components/           shell, data table, badges, empty/error states (shadcn/ui based)
      features/
        ask/  queue/  decision-card/  approvals/  identity-review/  status/
      styles/tokens.css
    dist/                   build output (git-ignored; built by `make ui` / `npm run build`)
tests_ui/
  conftest.py               a tiny temporary warehouse (never the real one)
  test_api.py, test_security.py, test_integration.py (real modules, opt-in)
```

`tests_ui/` sits outside `tests/` so the backend suite is untouched; it can be folded in later.

---

## 5. API surface (v1)

All responses carry `as_of_date`. All `GET`s are side-effect free (apart from the access log, §9.5). The user
comes from a **server-side session** (§9.1), never from a header or field the browser controls;
`deps.current_user` resolves it through `agent_view.staff(con, id)` on every request, so a deactivated member of
staff loses access immediately.

Session endpoints, in addition to the table below: `POST /api/session/login` (staff ID + password; production:
SSO callback), `POST /api/session/logout`, `GET /api/session/csrf`.

| Method & path | Calls | Notes |
|---|---|---|
| `GET /api/session/me` | `nba.agent_view.staff(con, id)` | role, team, certified flag |
| `POST /api/ask` `{question}` | `ask.service.ask(question, UserContext(...), as_of_date=AS_OF, con=con, llm=..., rag=...)` | returns `AnswerCard` as JSON; role mapped per §7 |
| `GET /api/queue/mine` | `nba.agent_view.agent_queue(con, me, AS_OF)` | agent view |
| `GET /api/queue/digest` | `nba.agent_view.lead_digest(con, AS_OF)` | supervisor view |
| `GET /api/cases/{rec_id}` | `nba.agent_view.card(con, rec_id, me)` + `case_context(con, case_id, AS_OF)` | 403 on `AccessDenied` |
| `GET /api/review/reasons` | `nba.review.REASON_CODES`, `APPROVERS` | one source of truth for dropdowns |
| `POST /api/review/{rec_id}` `{decision, reason_code?}` | `nba.review.record(...)` | 409 on `ReviewError` |
| `POST /api/review/batch` `{rejected: {rec_id: reason}}` | `nba.review.approve_batch(...)` | |
| `GET /api/review/summary?from&to` | `nba.review.summary(con, start, end)` | weekly override review |
| `GET /api/identity/queue` | `SELECT ... FROM gold.identity_review_queue` (read) | published to gold by the identity build |
| `POST /api/identity/decisions` | append to `identity_decisions.csv` | `reviewer` = signed-in user (from the session) |
| `GET /api/status` | reads `reports/*.md` headers + row counts of gold tables | optional page |

DataFrames are converted with `df.to_dict(orient="records")` plus explicit date/Decimal handling in `schemas.py`.
OpenAPI (`/api/docs`) generates the TypeScript types (`openapi-typescript`), so front and back cannot drift.

---

## 6. Screens

The tone is a bank's internal tool: quiet, dense, exact. No emoji, no gradients, no illustrations.

### 6.1 Shell (every page)

- **Top bar:** product name, global "Data as of 2026-…" pill (always visible), signed-in user with role badge
  and a Sign out button, environment tag `DEMO` (amber; always visible while demo login is on).
- **Idle lock:** after 15 minutes without activity the screen locks and the session ends (§9.1); after 13
  minutes a dialog warns and offers "Stay signed in".
- **Left nav** shows only what the role can use: Ask (all), My Queue (agent), Team Queue & Approvals
  (team lead, hardship specialist), Identity Review (analyst / data steward), Data Status (all).
- **Busy banner** for `503 warehouse_busy`; **not connected** panel for `501`.

### 6.2 Ask

- One large question box with 3–4 example questions under it (chips).
- Answer card, top to bottom: the answer in plain text → **provenance badge** (`Certified metric` green-grey,
  `Generated SQL` amber, `Policy answer` blue, `Refused` neutral) → result table (TanStack Table, max 50 rows,
  CSV download) → collapsible "How this was answered": metric id and parameters, or the SQL, and sources →
  notes/caveats.
- Refusals show the reason in plain language and a suggested rephrase where `ask()` gives one.
- History of the last 10 questions in the session, held in page memory only (gone on reload or sign-out;
  never in `localStorage`, §9.4).
- The answer text is rendered as **plain text**. LLM output is never rendered as HTML (§9.6).

### 6.3 Agent Queue

- Table: priority order exactly as `agent_queue` returns it (do not re-sort client-side by default).
  Columns: action, customer/case ref, risk band, expected value, state badge, approval needed.
- State badges reuse the Streamlit labels (`ready`, `waiting_agent`, `waiting_approval`, `blocked`,
  `overridden`) so both UIs say the same thing.
- Row click opens the Decision Card. Keyboard: ↑/↓ to move, Enter to open.

### 6.4 Decision Card

Two columns.

- **Left (the decision):** recommended action, one-sentence summary, top 3 reasons (from `explain`), "why this
  over the runner-up", risk band, blocked actions with the rule that blocked each, hardship options table.
- **Right (the case):** facts, recent contacts, promises (from `case_context`), decision history.
- **Action bar (sticky bottom):** Accept · Override (reason required) · and for approvers Approve · Reject
  (reason required). Buttons appear only when `review.APPROVERS` / assignment allows; the server re-checks anyway.
  After a write, the card refetches and shows the new state; no optimistic update (correctness over speed).

### 6.5 Approvals (team lead / hardship specialist)

- Digest tiles: pending approvals, digital batch size, overrides this week.
- "Approve digital batch" with a review step: the list of cases in the batch, tick to pull any out with a reason,
  then one confirm dialog stating the count.
- Weekly override summary table.

### 6.6 Identity Review

- Queue table: reason, source systems, Splink probability, LLM verdict.
- Pair view: record A and record B side by side, field by field, differences highlighted; Splink explanation
  line; LLM verdict and reason, labelled "AI second opinion".
- Decision: Match · No match · Unresolved, optional note. Message after saving: "Saved. Applied on the next
  identity run."

### 6.7 Data Status (optional)

Read-only: last pipeline run, publish gate pass/fail, DQ and anomaly report headlines, links to the full
reports in `reports/`.

---

## 7. Roles

Two role lists exist today and do not match:

| Staff role (`agents.role`, used by NBA) | Ask role (`ask.types.ROLES`) |
|---|---|
| `agent` | `agent` |
| `team_lead` | `team_lead` |
| `hardship_specialist` | `team_lead` (proposed) |
| `qa_analyst` | `analyst` |

The mapping lives in `ui/tiui/deps.py` only. Confirm with P2 (Q3).

---

## 8. Visual design system (CIBC brand colours, light)

### 8.1 Principles

- Look like the bank's own web pages, not a generic dashboard: white panels on a light grey page, thin 1px rules,
  4px corners, a dark-grey horizontal menu, a red rule across the top, regular-weight headings, ledger tables.
- No decorative icons, gradients, glows, uppercase "eyebrow" labels or icon-and-number cards.
- Colour carries meaning: red for primary actions and links; status labels always carry text.

### 8.2 Colours and type (from cibc.com, read from the live site's computed styles on 2026-10-04)

| Token | Value | Use on cibc.com / here |
|---|---|---|
| `--brand` | `#C41F3E` | primary buttons, links, the top rule, the active menu item |
| `--brand-dark` | `#8B1D41` | hover on buttons and links, focus outline |
| `--danger` | `#AF0B1C` | failures |
| `--foreground` | `#333333` | body text |
| `--charcoal` | `#383B3E` | menu bar and footer |
| `--panel` | `#F2F3F2` | page background, panel title bars, table headers |
| `--input` / `--border` | `#BBBCBC` / `#D8D8D8` | input borders, rules |

Status label backgrounds use the site's tints (`#F5E3E8`, `#DEE9F7`). The site's typeface (Whitney) is licensed,
so the UI uses the site's own fallback, Arial / Helvetica; no web fonts are downloaded. Buttons follow the site:
solid red with a 2px border and 4px corners, outlined red for secondary actions. This is a hackathon build for
CIBC: it uses the colours only, not CIBC's name or logo, and it is not presented as an official CIBC product.

Components are shadcn/ui (Radix), copied into `ui/web/src/components/ui` and restyled with these tokens.

### 8.3 Layout

- Header: product wordmark, `DEMO ENVIRONMENT` tag when on, business date, and the signed-in person's name,
  staff ID and role (menu: *My profile and access*, *Sign out*). Below it the charcoal menu bar.
- *My profile and access* shows only the signed-in person's own record: staff ID, role, team, reporting line,
  site, employment type, languages, vulnerable-customer certification, access and session.
- Footer (charcoal): internal-use notice and the session timeout.

### 8.4 Formats

| Kind | Format | Standard |
|---|---|---|
| Date | `2026-10-03` | ISO 8601 |
| Timestamp | `2026-10-03 14:05 IST` on screen; stored and sent by the API as `2026-10-03T08:35:00Z` | ISO 8601 (UTC in the API, local time zone shown on screen) |
| Money in a table | header `Balance (CAD)`, cells `1,234.56` right-aligned | ISO 4217 code in the header |
| Money in a sentence | `CAD 1,234.56` | ISO 4217 code, `Intl.NumberFormat('en-CA', {style: 'currency', currency: 'CAD', currencyDisplay: 'code'})` |
| Negative money | `-1,234.56` (in `--danger` only if it is a problem, not just because it is negative) | |
| Percent | `12.4%`, one decimal | |
| Large counts | `1,048,576` | |

A bare `$` is not used: in a Canadian bank it does not say whether the amount is CAD or USD. When French is
added, the same formatter with `fr-CA` gives `1 234,56 CAD`, so no format code changes.

### 8.5 Accessibility

- WCAG 2.2 AA contrast with the tokens above; visible 2 px `--accent-text` focus ring on every control.
- Every action reachable by keyboard; tables navigable with arrow keys; dialogs trap focus and close on Esc.
- `aria-live="polite"` for Ask progress and save confirmations; badges have text, not just colour.
- **Language:** sentence case, plain verbs ("Approve", not "Submit"); every AI-written text is labelled as such.
- **Bilingual:** strings go through a small `t()` helper from day one, English only in v1 (French is an easy
  later add since policy RAG is already EN/FR).

---

## 9. Security

Threat model, briefly: the users are bank staff on an internal network. The things to protect are customer PII,
the integrity of decisions (who approved what), and the LLM budget. The likely attacks are a user reaching data or
actions outside their role, cross-site attacks from another tab (CSRF, clickjacking, XSS), prompt injection
through questions or customer notes, and data left behind in the browser or in logs.

The demo cannot have real SSO, so §9.1 separates what is demo-only from what is built properly now. Everything
else in this section is built in v1.

### 9.1 Authentication and sessions

Two roles use the app, chosen on the sign-in page: **Team lead** and **Agent** (agents and senior agents). Other
staff roles have no access.

| Aspect | v1 | Production path |
|---|---|---|
| Team lead accounts | Created by an administrator on the server (`python ui/accounts.py add <staff id>`), team leads only | The bank's SSO (OIDC) with MFA |
| Agent accounts | The agent's own team lead sets the password on the *Agent accounts* page, for agents who report to them (`curated.agents.team_lead_id`). It is fixed: agents cannot change it; only their lead can reset it or remove access (which also signs them out) | SSO; the lead's page becomes access requests |
| Sign-in check | Staff ID + password, and the staff ID's real role must match the chosen tab. A mismatch fails exactly like a wrong password | Same |
| Before sign-in | Nothing about any member of staff is available: no directory, no search, no hints | Same |
| Passwords | scrypt hashes (n=2^14, r=8, p=1, 16-byte salt) in `warehouse/ui_accounts.json`, outside git, with when and by whom each was set; 12–128 characters, not letters-only or digits-only; never returned by the API or logged | Held by the identity provider |
| Failed sign-in | One message for every failure, equal work whether or not the ID exists; account locks for 15 minutes after 5 failures; 5 attempts per minute per address | Identity provider's policy |
| Session | Server-side store; browser holds only an opaque random id in an `HttpOnly; SameSite=Strict` cookie (`Secure` over HTTPS); new id on every sign-in | Same, shared store |
| Timeouts | 15 min idle, 8 h absolute; logout deletes the server record | Same |

The API never trusts a user id sent by the browser: every request re-reads the signed-in person from the session
and re-checks them against `curated.agents` (still active, still in the role they signed in with).

### 9.2 Authorisation

- Deny by default: every router depends on `deps.current_user`; an endpoint without an explicit role rule
  returns 403. A test lists all routes and checks this.
- Role rules live on the server only. Hiding a button in React is for convenience, not a security control.
- Reuse the backend's own checks so there is one rule set: `agent_view.can_see` / `card` (raises
  `AccessDenied`) for reading cases, `review.check` inside `review.record` for decisions.
- Object-level checks: `/api/cases/{rec_id}` is checked against the signed-in user on every request, so
  guessing an id gives 403, not data. Ids are opaque recommendation ids, not customer numbers.
- Identity review is limited to an explicit list of data-steward ids in `ui/tiui/settings` until roles exist
  for it (part of Q2).

### 9.3 Browser and transport hardening

Set by one middleware on every response:

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'` |
| `X-Frame-Options` | `DENY` (older browsers) |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), clipboard-read=()` |
| `Cross-Origin-Opener-Policy` / `-Resource-Policy` | `same-origin` |
| `Cache-Control` on `/api/*` | `no-store` (customer data is never cached by the browser or a proxy) |
| `Strict-Transport-Security` | `max-age=31536000` when served over HTTPS |

- Vite must build with no inline scripts or styles, so the CSP needs no `unsafe-inline`. No third-party CDNs,
  fonts or analytics.
- **CSRF:** `SameSite=Strict` plus a per-session CSRF token (`GET /api/session/csrf`) sent as `X-CSRF-Token` on
  every `POST`; the server compares it in constant time. Requests whose `Origin` is not the app's own are refused.
- **CORS:** off. The SPA and API share one origin; in development the Vite proxy keeps it that way.
- **Network:** uvicorn binds to `127.0.0.1` by default. Exposing it on a LAN needs a TLS-terminating reverse
  proxy and is a deliberate config change, not a default.
- `/api/docs` (OpenAPI UI) is disabled unless `UI_DEV=1`.
- Session cookies are `HttpOnly`, so page scripts cannot read them (checked in the browser: `document.cookie` is empty).

### 9.4 Customer data (PII)

- **Mask on the server, by role**, before data reaches the browser: `governance.masking.policy_for(columns,
  role, table)` for tables and `mask_text(text, role)` for notes and transcripts (P2's module; default is hide).
  Until that module is merged the API masks every column that `config/sensitivity.yaml` marks `protected`,
  `direct_pii` or `quasi_identifier` (or lists as a proxy). If P2's policy call fails for any reason, every
  column is masked (fail closed). Phones in the identity review are shown only as their last four digits.
- **Least data:** each endpoint returns only the fields its screen shows (Pydantic response models with explicit
  fields, so a new warehouse column never leaks automatically).
- **Nothing persistent in the browser:** no PII in `localStorage`, `sessionStorage`, IndexedDB or the service
  worker (there is none). TanStack Query cache is memory only and is cleared on sign-out and idle lock.
- **No PII in URLs:** routes use opaque ids (`/cases/REC-…`), never names, phone numbers or account numbers.
  Ask questions are sent in a `POST` body, never a query string.
- **No PII in logs:** request logs record method, route template, status, latency, user id and a request id.
  Bodies are never logged. Exceptions are logged server-side and the client gets only
  `{"code", "message", "request_id"}`, no stack traces.
- **Exports:** the Ask CSV download is of already-masked rows and is itself an audited event (§9.5). Cells
  starting with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'` (CSV formula injection).
- **Print:** the print stylesheet adds the user id, timestamp and "Internal — contains customer information"
  footer to every page.

### 9.5 Audit trail

- Every **write** already lands in an append-only record (`gold.nba_overrides`, `identity_decisions.csv`).
- The UI adds an **access log** in `warehouse/ui_audit.duckdb` (`ui.access_log`: ts, user id, role, action,
  object id, request id, outcome). Logged: sign-in/out, failed or denied requests, every Decision Card opened
  (viewing a customer's case is an access to PII), every Ask question and its path, every export.
- LLM calls are audited through `common.audit.AuditLog` (§3.3).
- The log is append-only from the API's side (no update or delete endpoint) and is never shown in the UI to
  roles other than QA analyst.

### 9.6 Input handling and AI safety

- **Validation:** Pydantic models with limits on every input: question ≤ 500 characters; `decision` and
  `reason_code` are enums built from `review.REASON_CODES`; identity notes ≤ 500 characters with newlines and
  control characters stripped; ids must match their pattern. Request bodies over 32 KB are refused.
- **SQL:** the API never builds SQL from user input. The only SQL it runs itself (the identity queue read) is a
  fixed statement with bound parameters. Questions go through `ask()`, which already screens for injection
  (`ask/injection.py`), refuses out-of-scope questions (`ask/refusal.py`) and validates generated SQL with
  sqlglot (`ask/data/validator.py`).
- **LLM output is untrusted text:** rendered as plain text in React (no `dangerouslySetInnerHTML`, no Markdown
  renderer with HTML enabled). Links in answers are not made clickable. AI-written text is always labelled.
- **Rate limits** (per session, in memory): Ask 10 per minute and 200 per day (LLM cost and scraping guard);
  writes 60 per minute; login 5 attempts per minute per IP. Over the limit: `429` with a retry time.

### 9.7 Secrets and dependencies

- API keys stay in the environment / `.env` (already git-ignored) and are read only by `common/llm.py`. They are
  never sent to the browser, never logged, and the Vite build exposes no `VITE_*` secrets (a test greps
  `dist/` for key patterns).
- Dependencies pinned: `package-lock.json` with `npm ci`; exact versions in `ui/requirements.txt`. `npm audit`
  and `pip-audit` run before merge; high-severity findings block.
- The production build has no source maps served and no React dev tools hooks.

### 9.8 What v1 does not cover (say so in the submission)

Real SSO, phishing-resistant MFA through the bank identity provider, TLS certificates, a shared session store, network segmentation, encryption at rest for the
warehouse, and a formal penetration test. These belong to deployment inside the bank; the design leaves a clear
place for each (§9.1 production column, §9.3 reverse proxy).

The current implementation adds local authenticator MFA and recovery codes by default. See the implementation
update above for its provisioning process and the explicitly labelled shared demo exception.

---

## 10. Build and run

```
pip install -r ui/requirements.txt
cd ui/web && npm ci && npm run build      # Node 20+, only on the machine that builds
python ui/serve.py --demo                  # API + built SPA on 127.0.0.1:8000
```

Settings (environment variables, read once by `ui/tiui/settings.py`): `UI_DEMO` (shows the DEMO ENVIRONMENT tag), `UI_DEV` (OpenAPI docs and dev logging, default off), `UI_SESSION_IDLE_MIN` (15),
`UI_SESSION_MAX_HOURS` (8). Safe defaults: with nothing set, there is no way to sign in.

- During development: `npm run dev` (Vite on 5173) with a proxy from `/api` to 8000.
- A `make ui` target can be added by agreement; note the current `Makefile` uses `.venv/bin/...`, which does not
  exist on Windows (`.venv\Scripts\`). The `ui/README.md` gives both.
- `WAREHOUSE_DIR`: the default warehouse sits inside the repo, which here is inside OneDrive. Point
  `WAREHOUSE_DIR` outside OneDrive for the demo; sync clients and DuckDB lock files do not mix well.

---

## 11. Testing

- **API contract tests** (`tests_ui/`): FastAPI `TestClient` on a fixture warehouse (`common/fixtures.py`); one
  test per endpoint, plus the 403/409/501/503 mappings. Includes a test that no connection is left open after a
  request (open the file read-write from the test after each call).
- **Security tests** (`tests_ui/test_security.py`):
  - every route except login and health returns 401 without a session, and 403 for a role it does not allow;
  - an agent requesting another agent's recommendation id gets 403;
  - a `POST` without a valid CSRF token, or with a foreign `Origin`, is refused;
  - every response carries the §9.3 headers, and `/api/*` responses carry `Cache-Control: no-store`;
  - error bodies never contain a stack trace, SQL or a column marked PII;
  - masked roles never receive a `direct_pii` or `protected` column (checked against `sensitivity.yaml`);
  - over-limit requests get 429; an expired or logged-out session id is rejected;
  - `ui/web/dist` contains no API key patterns, no source maps and no inline `<script>`.
- **Frontend:** Vitest for the API client and formatters (ISO dates, `CAD` amounts, CSV formula escaping); one
  Playwright smoke test that walks sign-in → Ask → Queue → Card → Accept on the fixture data, and checks that
  nothing is written to `localStorage` / `sessionStorage`.
- **Non-interference check in CI / pre-merge:** `git diff --name-only main... | grep -v '^ui/\|^tests_ui/\|^docs/ui_'`
  must be empty for UI PRs.

---

## 12. Plan against the deadline (4 Oct, 21:00 IST)

| When | What | Done when |
|---|---|---|
| Today, 3 h | `ui/` scaffold, `deps.py`, connection spike (§3.1), sessions + CSRF + security headers (§9.1–9.3), dark tokens and shell (§8) | sign-in works, security tests for auth/headers pass, shell renders in the dark theme |
| Today, 3 h | Agent Queue + Decision Card read-only | parity with the Streamlit pages' content |
| Tonight, 2 h | Writes: accept / override / approve / reject / batch | write round-trips, `ReviewError` shown inline |
| 4 Oct morning, 3 h | Ask page | benchmark sample questions render correctly, refusals readable |
| 4 Oct, by 13:00 | Identity Review, masking + access log (§9.4–9.5), rate limits, polish, Playwright smoke | full demo path works on the real warehouse; all `tests_ui/` green |
| **4 Oct, 15:00** | **Go / no-go** (the proposal's cutoff) | demo whichever of Streamlit / React is better |

If time runs short, drop in this order: Data Status → Identity Review → Approvals batch review step.
**Security items are never dropped**: sessions, server-side authorisation, masking, headers and CSRF ship with
the first screen that shows customer data.

---

## 13. Open questions (need an owner's answer)

| # | Question | Owner |
|---|---|---|
| Q1 | Is serving the UI from the full warehouse fine for the demo, or should it read a copied "serving" file? | P1 / all |
| Q2 | ~~Where does the UI read the identity queue?~~ Answered: the build already publishes `gold.identity_review_queue`. | P1 |
| Q3 | Role mapping in §7, especially `hardship_specialist` for Ask. Interim answer: `hardship_specialist` maps to Ask role `agent` (policy and own cases); see `ui_decisions.md` D8. | P2 |
| Q4 | Which LLM and RAG settings does the live Ask use in the demo (`--llm` equivalent on or off)? | P2 / P4 |
| Q5 | Customer lookups: `ask()` takes a `resolver`; which function does the UI pass (P4's `customer_path`)? | P4 |
| Q6 | Accent colour and product name on screen. | team |
| Q7 | Is P2's `governance/masking.py` (with `config/masking.yaml`) the agreed masking rule set for the UI? | P2 |
| Q8 | ~~Which staff may use Identity Review?~~ Answered: team leads and QA analysts (QA now has its own sign-in); see `ui_decisions.md` §2. | P1 |

---

## Corrections to the stack proposal (from checking the code)

1. **Read-only does not remove the lock conflict.** DuckDB will not let a process write while another process has
   the file open, even read-only. The fix is short-lived connections (§3.1); SQLite alone does not solve it.
2. **Identity decisions are already outside the warehouse** (`data/review/identity_decisions.csv`). They do not
   need to move.
3. **Streaming needs backend work**: `ask()` returns a finished card and `common/llm.py` has no streaming.
4. **Model names:** "Claude Sonnet 5" is not a model id. Current ids are `claude-sonnet-5-5` and
   `claude-haiku-4-5`. The live config uses Nemotron on OpenRouter for both tiers.
5. **Embeddings already exist** without Chroma (P4 `retrievers.py`, P2 `fastembed`), so "drop Chroma" holds,
   but no DuckDB embedding store is needed. `docs/p4_design_sections.md` (D9) and `implementation_plan.md` §1
   still name Chroma and Great Expectations.
6. **`run_benchmark.py` already calls `ask()`**, so that part of step 3 is done.
7. **There is little Streamlit to keep**: only P3's Agent Queue and Decision Card exist; Home is a placeholder and
   there is no Ask page.
