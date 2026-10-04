# Banking console refinement and security record

Implemented on `ui/banking-polish-security`, 2026-10-04, and pushed to GitHub at the user's request.
Previous pushed baseline: **`3c87423`**. Published rollback tag: **`rollback-ui-3c87423`**.
The original `ui/react-app` branch remains at the baseline. No pull request or public deployment.

## Interface

Applied the installed `design-taste-frontend` skill's audit and preservation principles. Its marketing-specific
rules do not fit a staff banking console. Preserved the CIBC wordmark, red/burgundy accents, charcoal navigation,
existing route names, role choices and legal copy. Retained the existing shadcn/Radix components and Arial/Helvetica
fallback rather than introducing another design system. Design dials: variance 3, motion 2, density 5.

Refined spacing, typography, tables, input contrast and button sizing. Added clear pending/error states,
keyboard-accessible sign-in tabs, a visible sign-out action, a real session countdown and a separate security tab.
The initial app and authentication dialog load lazily. No new image or external font dependency.

## Access modes

**Individual credentials:** MFA required by default. Service-desk enrollment pairs an authenticator and issues ten
single-use 128-bit recovery codes. Secrets are encrypted with Fernet; recovery codes are hashed. OTP counters and
recovery codes are consumed atomically with file locking. Password reset preserves the second factor and invalidates
sessions. New passwords use scrypt n=2^17, r=8, p=1; previous n=2^14 hashes remain readable. Account writes are atomic
and owner-only. A small common-password blocklist supplements the existing length rules.

**Temporary shared demo:** `python ui/serve.py --demo --shared-access` prompts for one password without saving it.
Active staff are resolved directly from `curated.agents`: agents/senior agents choose Agent; team leads choose
Team lead. No individual passwords or hashes are read or written. Unknown IDs, inactive staff, mismatched roles and
unsupported roles are refused. Existing accounts and MFA keys are preserved. Per-person password changes and
revocation are disabled. This loopback-only mode visibly disables MFA; stopping it ends all demo sessions.
The inspected warehouse has 484 active agents, 135 active senior agents and 75 active team leads.

Latest follow-up: shared demo sessions now last one hour, as requested: 60 minutes idle and a one-hour absolute
limit. This supersedes the temporary no-expiry setting. The countdown uses server-provided durations anchored
to `performance.now()` instead of comparing the server's sign-in timestamp with the browser's wall clock.
Explicit sign-out, account-status checks, session revocation and the three-session cap remain active.
Restarting ends all sessions. Explicitly disabling expiry remains restricted to labelled demos.

Table browser: the demo's team leads can now see actual `curated` and `gold` tables through `/tables`.
The catalog includes row counts; each table shows paginated rows and its column types. Empty stubs keep
their column headers. Sensitive and unclassified values are masked in the SQL projection before reaching
the browser. Internal/audit tables are excluded; schema/table names are checked against the catalog, pages
are bounded, and the connection is read-only. Agent accounts and non-demo deployments are refused.

## Implemented controls

- Password plus authenticator/recovery authentication in the default mode, generic failures, account lockout and rate limits.
- Server-side role and team checks against DuckDB on every request, including account deactivation and credential-version checks.
- HttpOnly, SameSite=Strict cookies; Secure cookies and HSTS when HTTPS is configured; session/CSRF rotation after fresh verification.
- Three sessions per account, own-session visibility, sign-out of other devices, and recent security events restricted to the current account.
- Fifteen-minute idle and eight-hour absolute expiry. Passive polling cannot extend idle time; deliberate activity can renew it.
- Fresh verification before account changes, reviews, approvals, exports and ending other sessions; five-minute verification validity.
- CSRF and same-origin checks, cross-site request rejection, explicit host allowlisting, JSON-only bounded request bodies and security headers.
- Cross-tab sign-out, sensitive cache clearing and rejection of late responses belonging to a previous session.
- Existing server-side masking, strict CSP and API no-store policies; export formula protection and generic errors without SQL/PII tracebacks.
- Audit events for authentication, verification, refusals, session termination and sensitive changes, excluding passwords, OTPs and session secrets.

## Verification

- Backend: **85 passed, 3 skipped**. Skips concern unmerged real P2/P3 integrations.
- Frontend: **14 passed**; TypeScript and production build passed.
- `npm audit` and `pip-audit -r ui/requirements.txt`: no known dependency vulnerabilities at review time.
- Chromium: authenticator sign-in, 32-character recovery challenge, sensitive-action cancellation and verified retry,
  cross-tab sign-out, shared access using actual DuckDB agent/lead IDs, and 390/768/1280px layouts passed. No runtime errors.
- axe WCAG checks: no reported violations on sign-in, agent accounts or the session security panel.
- Local Lighthouse desktop sign-in: performance 99, accessibility 100, best practices 96; LCP 0.8s, CLS 0.002.
  Best-practices deduction reflects the expected anonymous `/api/session/me` 401. These are local laboratory results.
- Shared-access browser verification confirmed the existing account-file digest and warehouse size/mtime remained unchanged.
- Follow-up: a simulated 24-hour backend session stayed valid without automatic expiry; Chromium showed no
  countdown or idle warning after advancing 16 minutes, and explicit sign-out still succeeded.
- One-hour update: a backend session survives the old 15-minute cutoff and expires at exactly 3600 seconds,
  including after activity. Chromium checks login with a browser clock one day ahead and delayed expiry polling.
- Table browser: Chromium verified the actual 1,000-row agents table, paging, column types, empty NBA stub
  headers, direct-link sign-in and mobile layouts. axe reported no violations and no runtime errors occurred.

## Deployment work

Database schema stubs already exist in `contracts/interfaces/stubs.sql`. Inspection found `gold.nba_decisions`,
`gold.nba_overrides` and `gold.promises_enriched` present with zero rows. Ask/queue screens remain unavailable
because `trustissues.ask.service`, `trustissues.features` and `trustissues.nba` are absent on this branch.
This UI work did not add or populate banking data tables; agent accounts read the existing `curated.agents`.

This is application hardening, not a claim of bank certification. Before a bank deployment, integrate the bank's
OIDC/SSO and phishing-resistant authenticators; provision TLS and trusted reverse-proxy headers; configure explicit
`UI_ALLOWED_HOSTS` and `UI_HTTPS=1`. These environment settings do not create TLS.

Session stores, login lockouts and rate limits currently live in one process. Run a single process until replaced
with shared stores. Move MFA encryption keys into the secret manager; implement key rotation with re-encryption.
Use bank recovery/identity-proofing procedures and a comprehensive breached-password check. Integrate off-host
audit collection, alerts and user notifications, storage encryption, protected backups and network controls.
Complete an independent security review and penetration test. Financial transaction authorization belongs to the
banking backend; this console does not implement payment or transfer APIs.

Reference guidance: [OWASP authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
[MFA](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html),
[password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html),
and [NIST authentication guidance](https://pages.nist.gov/800-63-4/sp800-63b.html).

## Rollback

The original `ui/react-app` branch remains at `3c87423`. Stop the UI server, switch to that branch and rebuild:

```sh
git switch ui/react-app
cd ui/web
npm ci
npm run build
```

Restart using the original branch's normal server command. The published `rollback-ui-3c87423` tag records the exact
previous push. Avoid `reset --hard`: unrelated local files must be preserved.

The verification and previews did not reset real staff credentials. Before any future deployed credential
changes, back up accounts and the matching MFA key securely. The baseline's older scrypt memory limit cannot
verify newly generated stronger hashes: a code rollback alone does not roll back account data. Plan credential
compatibility explicitly and preserve audit and decision history. Restarting ends all in-memory sessions.
