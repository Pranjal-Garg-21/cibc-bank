# TrustIssues UI: decision log and the logic behind it

Living record of **what we decided about the web UI and why**. Newest decisions go at the bottom of each section; nothing is
deleted, a changed decision gets a new entry that says which one it replaces. The original design (`ui_design.md`) is
the baseline; where it disagrees with this file, this file is current. Security controls and test evidence are in
[`ui_security_review.md`](ui_security_review.md); how to run it is in [`../ui/README.md`](../ui/README.md).

Last updated: 2026-10-04. Branch: `ui/banking-polish-security`.

**How to add an entry:** copy the template at the end, give it the next number, say what it replaces if anything, and
link the files that implement it. Record the *reason* and the *alternative you rejected*; the code already says *what*.

---

## 0. Contents

1. [Principles](#1-principles)
2. [Who uses the system and what each role is for](#2-who-uses-the-system-and-what-each-role-is-for)
3. [Access control decisions](#3-access-control-decisions)
4. [Screen decisions](#4-screen-decisions)
5. [Security decisions that shape the UI](#5-security-decisions-that-shape-the-ui)
6. [The demo and preview](#6-the-demo-and-preview)
7. [Mistakes we found and what they taught us](#7-mistakes-we-found-and-what-they-taught-us)
8. [Rejected alternatives](#8-rejected-alternatives)
9. [Known limits and open questions](#9-known-limits-and-open-questions)
10. [Where things live](#10-where-things-live)
11. [Template](#11-template)

---

## 1. Principles

These are the rules every later decision is checked against.

| # | Principle | What it means in practice |
|---|---|---|
| P1 | **Built around the use case, not the data model.** | The product is *collections decisioning with a human in the loop*: recommend the right treatment, channel and timing; a person accepts or overrides; risky cases get a second sign-off; everything is auditable. Each tab exists because a role needs it for a step of that loop. |
| P2 | **The server decides, the browser shows.** | Permissions, scoping and what a role may see are enforced on the server for every request. The menu hides what you cannot use, but hiding is a convenience, never the protection. |
| P3 | **Least privilege and need-to-know.** | A role gets the smallest set of screens and customers its job needs. Extra access is a risk to customers and to the bank, even if it is convenient. |
| P4 | **Separation of duties.** | The people who act on a case, approve it and audit it are different roles with different powers. No role can both do something risky and sign it off. |
| P5 | **Say nothing you do not have to.** | Refusals do not reveal whether something exists. Dashboards carry aggregates where identifiers are not needed. Errors carry a request id, never internals. |
| P6 | **Honest about the data.** | Every number shows its business date; stale or failed data is flagged to the people who rely on it; generated or uncertified answers are labelled as such. |
| P7 | **One look, one vocabulary.** | The same bank-style theme, the same table and panel components, the same labels (`labels.ts`) on every screen and for every role. Roles differ in *content*, not in how the product looks or speaks. |
| P8 | **Interactive by default, never hover-only.** | Figures and bars are links; filters live in the URL so they can be shared and survive the back button; tables sort and search; everything works from the keyboard. |
| P9 | **The UI never edits the backend.** | `ui/` is a client of `src/trustissues/`. Writes go through the backend's own functions; the UI adds checks, it does not replace rules. |

---

## 2. Who uses the system and what each role is for

### 2.1 The roles in the data

The staff table (`curated.agents`, 1,000 people) has exactly five roles:

| Role | People | Active | Certified for vulnerable customers |
|---|---|---|---|
| `agent` | 618 | 484 | 279 |
| `senior_agent` | 171 | 135 | 71 |
| `hardship_specialist` | 107 | 83 | 107 (all) |
| `team_lead` | 88 | 75 | 48 |
| `qa_analyst` | 16 | 10 | 9 |

### 2.2 Decision D1: five roles, four dashboards

**Decision.** Agents and senior agents share one dashboard. Team lead, hardship specialist and QA analyst each get their
own. That is four dashboards and four sign-in tabs.

**Why.** We grouped roles by *what they are allowed to do*, taken from the backend rules, not by job title:

- `nba/review.py` (`APPROVERS`) lets a **hardship specialist** approve only `vulnerability` and `hardship` cases; `high_balance`
  and `digital_batch` are the team lead's alone. So specialists need a narrower approvals screen than team leads, and must
  never see the batch controls.
- A **QA analyst** is treated as a supervisor by the backend (can see recommendations) but approves nothing and, in Ask,
  is the `analyst` role: portfolio numbers yes, a single customer's text no. Their job is oversight, so they get
  aggregates and the audit log and no customer cases.
- A **senior agent** differs from an agent only in which cases they are given. Tools and permissions are identical, so
  a separate dashboard would be a copy.

**Rejected.** One dashboard for every supervisor (would show specialists a batch they cannot approve and QA account
management they must not have). Five dashboards (duplicates senior agent).

### 2.3 What each role's job is, and therefore its tabs

| Role | Job in the loop | Tabs |
|---|---|---|
| **Agent / senior agent** | Contact customers and work their own cases | Overview, My work queue, Next best action, Policy & case questions |
| **Team lead** | Run a team; give the second sign-off on risky actions; keep the team's decisions healthy | Overview, Next best action, Team & approvals, Decision review (own team), Agent accounts, Identity review, Policy & portfolio questions, Data quality, Tables (demo) |
| **Hardship specialist** | Protect customers in hardship or vulnerable situations | Overview, Next best action, Hardship approvals, Policy & case questions |
| **QA analyst** | Check that the system and the staff behave correctly, without acting on customers | Overview, Decision review (whole bank), Identity review, Policy & portfolio questions, Data quality, Audit log |

**Common to all roles:** an Overview as the first screen, the profile and session panel, the session timer, the same theme,
the questions screen (worded for the role), and the sign-out and idle-lock behaviour.
**Special to a role:** everything else in the table above.

---

## 3. Access control decisions

### D2: one capability table, deny by default

**Decision.** `ui/tiui/rbac.py` holds a single table mapping each role to named capabilities (`my_queue`, `approvals`,
`audit_log`, ...). Every route calls `rbac.require(user, capability)`. A role or capability that is not listed gets nothing.
The browser's menu and route guards are built from the same table through `GET /api/session/me`.

**Why.** Before this, role checks were scattered (`user.is_supervisor`, `require_role(SUPERVISORS)`, per-router tuples) and
too coarse: "supervisor" bundled team leads, hardship specialists and QA, so adding the two missing roles would have
silently widened access. Capabilities are named for the *job*, so a new screen cannot widen anyone's access by accident,
and one file answers "who can do X?" for a reviewer.

**Consequence.** To change a role's access you edit one table and the tests in `tests_ui/test_rbac.py` fail until the
matrix there is updated too, which makes access changes deliberate.

### D3: customer scope by role

`rbac.customer_scope(user)` returns the SQL fragment that limits `gold.nba_decisions` to the rows a role may open. Every
customer-level read uses it, so scoping cannot be forgotten on one screen.

| Role | May open | Why |
|---|---|---|
| Agent / senior agent | Recommendations assigned to them | They contact their own customers only. Mirrors `agent_view.can_see`. |
| Hardship specialist | Hardship, vulnerability and support-call cases | Their remit; nothing else is theirs to approve or advise on. |
| Team lead | All | The digital batch has **no agent** and is approved bank-wide, so a team-only scope would hide cases they must sign off. |
| QA analyst | None | QA audits behaviour from aggregates and logs. Opening a customer is an access to personal data they do not need. |

### D4: a case you may not see answers 404, exactly like one that does not exist

**Decision.** Out-of-scope cases and customers return 404 "not found", not 403 "not assigned to you".

**Why.** A different answer tells the asker the case *exists*, which is information about a customer they have no business
knowing (an "existence oracle"). We found the original backend adapter leaking this (403 vs 404) and fixed it; a test
asserts the two responses are byte-identical.

### D5: separation of duties

| Rule | Where enforced |
|---|---|
| Agents decide only recommendations assigned to them, and approve nothing | `rbac` (`decide` only for agents) + backend `review.record` |
| Hardship specialists approve only hardship and vulnerability | backend `APPROVERS` + UI scope (cases outside remit are 404) |
| Only team leads approve high balances and the digital batch | `approve_batch` capability + backend |
| QA can change nothing and never opens a case | no `decide`/`approvals`/`case_view`/`next_action` capability |
| Approvals need a fresh password check (and MFA code when enabled) | `require_reauthentication` (existing control) |

**Why.** The risky actions in collections (contacting a customer in hardship, a large balance, a bulk send) are exactly the
ones where one person acting alone causes harm. The design forces a second person, with a *narrower* power than the first.

### D6: sign-in: one tab per role, a wrong tab fails like a wrong password

**Decision.** The sign-in page has four tabs: Agent, Team lead, Hardship, QA. Choosing the wrong tab for your account returns
the same error and timing as a wrong password.

**Why.** The tab is a claim about who you are; if a wrong claim got a different message ("this is a team lead account"),
the sign-in page would become a staff directory. Four tabs (not a role dropdown from the server) also means nothing about
staff is fetched before authentication.

### D7: how accounts are created

Team leads, hardship specialists and QA analysts are created by an administrator (`python ui/accounts.py add <id>`); agents'
passwords are set by their own team lead in the app. **Why:** a team lead already owns a team and has a re-authenticated
path to manage it; specialists and QA belong to no team lead, so an administrator is the only legitimate source of trust.

### D8: decisions where we picked a default and recorded it

These were open questions; a reviewer may overrule them.

| Question | Default chosen | Reason |
|---|---|---|
| Should team leads see only their team's customers? | No: all customers, but **decision review is team-scoped** | The batch is bank-wide. Team scoping applies where the data is the team's own (override behaviour, agent workload). |
| Do hardship specialists get a work queue? | No: they review and approve | Support calls are assigned to certified agents; specialists' value is the second sign-off. Revisit if specialists are assigned calls. |
| What may a specialist ask in Ask? | Policy and their own cases (Ask role `agent`) | Portfolio numbers are not needed for their job (P3). |
| May QA look up an individual customer? | No | Aggregates are enough to audit behaviour; fewer customer accesses to protect (P3, P5). |

---

## 4. Screen decisions

### D9: every role starts on an Overview, described by the server

**Decision.** `GET /api/overview` returns a small description of a page for the signed-in role: *tiles* (key figures), and
*sections* (`bars` or `table`). The browser draws it without knowing the role (`Overview.tsx`).

**Why this shape.**
- **The server is the enforcement point (P2).** A role's page is assembled from its own scope, so the browser never receives a
  figure the role may not see. A client-side "hide the QA widget" approach would still ship the data.
- **One renderer, four dashboards.** New role content needs no new frontend page, and the dashboards cannot drift apart visually (P7).
- **Interactivity comes free (P8).** A tile, bar or table row can carry a link (`to`/`_to`), so figures lead to the filtered
  screen behind them (e.g. "Waiting for your decision: 10" opens the queue filtered to those 10).

| Role | Headline question it answers | Tiles | Sections |
|---|---|---|---|
| Agent | What do I do next? | Recommendations, waiting for my decision, ready, my override rate (7 d) | **Up next** (cases needing me, most urgent first), treatments, channels |
| Team lead | Does my team need me? | Case approvals waiting, digital batch waiting, agents still to decide, team override rate | Where the day stands, approvals by type, **team table** |
| Hardship specialist | Who needs care first? | Waiting for my approval, vulnerability, hardship, support calls | **Next to review** (highest risk first), why a support call is due, programmes on the cards |
| QA analyst | Is the system behaving? | Override rate, safeguard overrides, failed data rules, refused/failed requests (24 h) | Overrides by reason, recommendation mix, risk-score bands |

Ordering is deliberate: for agents it follows the queue rule (support calls, then calls, then other contact, then waits),
because duty of care comes before collection.

### D10: "Next best action" tab: three pillars from one search

**Decision.** A tab after "My work queue" with a customer-ID search on the left and, for the selected customer, three
side-by-side panels: **Right treatment**, **Channel**, **Timing**.

**Where each pillar comes from** (so nothing is invented in the UI):

| Pillar | Source in `gold.nba_decisions` | What the panel adds |
|---|---|---|
| Treatment | `action` | The runner-up action and why it lost, hardship options to offer first, top risk reasons |
| Channel | `channel` | What policy ruled out today and which rule (`blocked_actions`) |
| Timing | `decision_card.contact_window`, `action`, `requires_approval` | The contact window for live channels; "Not today" for a wait; "no time-of-day limit" for letters and digital reminders; whether approval gates the send |

**Why.**
- **Search-first** matches how an agent works a call: they have a customer on the line or in mind, not a list.
- **Three panels, same order as the question** ("what, how, when") so the answer reads left to right.
- **It reads `gold.nba_decisions` directly** instead of importing P3's module, so it works on any warehouse that has the table
  and says "not connected" if it does not.
- **One contact per customer:** a customer with several cases shows the case the system will actually contact on first,
  labelled "today's contact", with the others one click away. This mirrors `nba/queue.py`'s one-contact rule so the screen
  never recommends two contacts to one person.
- **Scoped by D3**, with the search matching only customers the role may open.

### D11: "needs approval" is independent of what the agent has done

**Decision.** An approval is *pending* when the recommendation requires one, no approval decision has been recorded, and the
agent has not overridden it. It does **not** matter whether the agent has accepted yet.

**Why.** Approval and agent decision are two separate sign-offs (`nba/review.py` keeps them as separate decision kinds; the
latest of each kind wins). An earlier version derived "pending approval" from a single combined state in which "agent to
decide" took precedence, so every hardship, vulnerability and high-balance *call* disappeared from the team lead's
approvals until the agent acted, and the team lead saw "0 waiting". See §7.

### D12: Team & approvals and Hardship approvals are one screen that adapts

The page reads the role's `approve_batch` capability: team leads get the status strip, the digital batch panel and the
override review; specialists get only the hardship and vulnerability list, with no batch and no bank-wide counts (the API
removes them, not just the page). The list has one filter chip per approval type the role may give, driven by the URL
(`?reason=hardship`) so the Overview bars can link straight to it.

### D13: work queue filters and sorting

Filter chips for status and treatment, plus a free-text filter and sortable columns (`DataTable`, used everywhere).
**Why:** a queue of 40-plus cases is worked by *state* ("what still needs me?") far more than by a single order, and
chips make that one click without a modal. Filters live in the URL so a link from the Overview and the back button both work.

### D14: Decision review

**What it shows.** Decisions, override rate, safeguard overrides, approvals given and rejected; decisions per day (grey =
accepted, red = overridden); why agents override (clickable); a per-agent table.

**Logic behind the choices.**
- **Overrides are information, not failure.** The reason codes (`customer_already_paid`, `hardship_disclosed`, ...) tell the
  model team where the card is wrong. So the page leads with *why*, not *who*.
- **"Duty of care" badge** on `hardship_disclosed`, `vulnerability_concern`, `dispute_raised` (the backend's
  `SAFEGUARD_REASONS`): these mean the customer needed more care than the card assumed, which is more serious than
  "customer already paid".
- **Outlier flag ("Review")**: an agent is flagged when they have at least **5 decisions** and an override rate of at least
  **max(25%, 1.5 x the overall rate)**. The sample floor stops one override out of two decisions looking like a trend;
  the floor of 25% stops a very low overall rate from flagging everyone; the 1.5x multiplier means "unusually far above the
  group". We first set the multiplier at 2x and the floor at 30%, which missed a plainly high agent (37% against 20%), so
  we relaxed it. It is a prompt for a conversation, never an automatic judgement, and is worded that way.
- **Scope:** a team lead sees their own agents and their own approvals; QA sees the bank. Same screen, different scope, so
  the team lead gets the coaching view and QA the oversight view.
- **Period selector (7/14/30 days)** because a week is noisy for small teams and a month hides a recent change.

### D15: Audit log (QA only)

- **Who:** QA analysts only (as the original design stated in §9.5). Team leads manage people, not the audit trail; a person
  who can approve should not also be able to read, and so be tempted to question, the record of their own approvals.
- **Opening it is logged** (`view_audit_log`), so oversight of the overseers is on record.
- **Filters** (period, outcome, action, staff id, free text) use bound SQL parameters, are length- and character-limited
  (anything odd is a 422), and the endpoint is rate-limited.
- **"Refused or failed"** is one click because that is the question QA actually asks first.
- **"Go live"** refreshes every 10 s for incident watching; off by default so an unattended screen does not poll.

### D16: Data quality is not for agents; a banner is

**Decision.** Agents and specialists do not get the Data quality tab (the API refuses it). If today's data refresh failed or
did not run, every non-supervisor page shows one yellow notice with plain advice; when all is well they see nothing.

**Why.** Rule ids and table counts are not something an agent can act on, and it is noise next to their work (P3). The one
thing they need is "can I trust today's recommendations?", so that is all they are told. The endpoint (`/api/status/freshness`)
returns only ok / not ok and a message.

### D17: the questions tab is named for what the role can ask

The backend (`ask/access.py`) allows policy and own-case questions to agents; portfolio numbers, generated queries and "why
did it change" only to team leads and analysts. So **agents and specialists see "Policy & case questions"; team leads and QA
see "Policy & portfolio questions"**. The old name "Ask" said nothing about purpose and, for agents, "data questions" would
have promised something they cannot do.

### D18: Interactivity conventions

- Filters, periods and selections live in the **URL** (shareable, survive reload and the back button).
- Tiles, bars and table rows are real links or buttons, with focus styles; rows are keyboard-operable.
- Tables sort and, past a few rows, search. Sorting is client-side on already-authorised data.
- Charts are plain SVG/CSS with a text equivalent (`<title>` on each bar); colour is never the only signal (labels and badges carry text).
- Overview data refreshes every minute and has a Refresh button; nothing else polls unless the user turns it on.

### D19: look and feel

CIBC brand colours, white panels on light grey, thin rules, ledger tables, small rectangular status labels, a dark-grey
menu bar with a red rule on top (`ui_design.md` §8.1-8.2). The original design planned a dark default theme; the built
interface is light, to look like the bank's own sites. **This replaces the dark-theme goal in `ui_design.md` §1 and §8.**

---

## 5. Security decisions that shape the UI

| # | Decision | Reason |
|---|---|---|
| D20 | Server-side sessions in an `HttpOnly; SameSite=Strict` cookie; nothing in browser storage | Nothing for a script to steal; sign-out clears query data in all tabs |
| D21 | Production session: 15 min idle, 8 h absolute (idle cap 30 min). **Demo only:** 2 h idle and absolute, cap 120 min | A bank-grade default for real use; the demo needs to survive a presentation. The demo allowance is enforced by `settings.py` and does not apply outside `--demo`. |
| D22 | Re-enter the password (and MFA code) for approvals, batch approval, password resets and access removal | These are the actions with consequences for a customer or for security |
| D23 | Customer IDs and case IDs are not masked; names, phones and other sensitive columns are, by role, on the server | IDs are pseudonymous join keys the work depends on; the sensitive columns come from `config/sensitivity.yaml`. QA's overview carries no customer or case identifiers at all (tested). |
| D24 | Every new read is parameterised SQL over `gold.nba_decisions` / `gold.nba_overrides`; no string-built queries from user input | Removes injection as a class; filters are also length/charset-limited |
| D25 | Rate limits per user: Ask 10/min, writes 60/min, new read endpoints 120/min, sign-in 5/min per address | Limits abuse and runaway scripts; sign-in limit also slows password guessing |
| D26 | Opened cases, decisions, Ask questions, audit-log views, refusals and sign-ins are written to the access log | The audit trail is only useful if reading a customer's case counts as an event |
| D27 | `Cache-Control: no-store` on all API responses; strict CSP; CSRF token and same-origin check on every write | Existing controls (`ui_security_review.md`); the new endpoints inherit them |

---

## 6. The demo and preview

### D28: a seeded preview with one account per role

`python ui/preview.py` builds a private, temporary, synthetic warehouse and starts the UI on it. It seeds:
- six staff accounts (three agents, a team lead, a hardship specialist, a QA analyst), **each with its own password**
  (listed in `ui/README.md` and printed at start), so every role can be tried and a wrong-role sign-in demonstrated;
- about 50 recommendations covering every action, channel and approval type, with realistic cards;
- two weeks of past decisions in which **one agent (`DEMO-A3`) overrides far more than the others**, so the outlier flag and
  Decision review have something real to show;
- today's partial progress (some calls accepted, one overridden) so the queue shows every state.

**Why separate and synthetic.** The real warehouse's `gold.nba_decisions` is empty until P3's job runs, and the preview must
never read real accounts or customers. Deterministic seeding (fixed random seed) means the same screens on every run, so
screenshots and tests are stable.

### D29: stand-ins for P3 and P2, used only by the preview

`ui/preview_backend.py` implements the parts of `trustissues.nba` and `trustissues.ask` the screens call, following the
same rules (who may accept, override, approve; which Ask path each role may use). **Why:** without them the queue, decision
card, approvals and questions screens would all say "not connected" and the demo would show nothing to interact with.
`ui/serve.py` never imports them, so the real server always uses the real modules. The first thing to do when P3 and P2
are merged is run the same role tests against the real modules.

### D30: how we checked it

- `tests_ui/test_rbac.py`: every role on every route (a matrix), customer scope, separation of duties, the 404 rule,
  audit-log behaviour and filters, role-specific Ask results. 42 tests; they run on the seeded data.
- Real browser checks: sign in as each role in headless Chrome, load every screen, and read the screenshots. This is how
  the approvals bug in §7 was found: the numbers on screen disagreed with the seed.

---

## 7. Mistakes we found and what they taught us

| What went wrong | How it was found | The rule we took from it |
|---|---|---|
| "Waiting for approval" showed 0 while hardship, vulnerability and high-balance cases needed approval | Reading the screenshot against the seed data | Derive each fact from its own source (the approval decision), not from a combined status that ranks another fact first (D11) |
| Another agent's case answered 403, a missing case 404 | Reviewing the case route while adding scoping | Never let "no access" and "does not exist" differ (D4) |
| The outlier flag missed an agent at 37% against a 20% average | Looking at the Decision review screenshot | Pick thresholds against realistic data, not round numbers (D14) |
| Team lead tab label wrapped onto two lines at four tabs | Screenshot of the sign-in page | Check at the real width after adding a tab |
| A "pending" count counted only `waiting_approval` state | Tests written from the rule, not from the code | Write the test from the policy sentence ("hardship cases need approval") |

(One apparent bug was in the *test script*, not the app: Radix tabs need a real mouse press, so a synthetic click never switched
tabs and the form was submitted with the wrong role. The app correctly refused it, which was a free test of D6.)

---

## 8. Rejected alternatives

| Alternative | Why not |
|---|---|
| One shared supervisor dashboard | Would show specialists the batch and QA the account management they must not use (D1) |
| Hide features in the browser only | Data would still be sent; any user could call the API directly (P2) |
| Role dropdown fetched from the server on the sign-in page | Fetches staff information before authentication (D6) |
| Team lead sees only their team's customers | The digital batch has no agent, so they could not sign off bank-wide sends (D3, D8) |
| QA analysts can open a customer for audits | More customer-data access than the job needs; aggregates suffice (D8) |
| Data quality on every role's menu | Not actionable for agents and a distraction; a freshness banner serves the real need (D16) |
| Hand-built frontend page per role | Four dashboards drifting in look and in what they leak; server-described pages give one renderer (D9) |
| Dark default theme (original plan) | The bank's own sites are light; replaced by D19 |
| Computing outlier from a fixed 30% override rate | Misses a high agent when the overall rate is low; relative threshold plus a floor (D14) |
| Showing the digital batch to specialists, disabled | A disabled control still discloses bank-wide volumes; the API now omits it |

---

## 9. Known limits and open questions

**Not built.**
- A **model and fairness monitoring** tab for QA: there is no data source on this branch. The QA overview shows recommendation
  mix and risk-score bands as a stand-in.
- Hardship specialists have no queue of their own (D8).
- Real SSO, off-host monitoring and an independent security review (see `ui_security_review.md`).

**Needs confirming with the owners.**
- P3: the real `review.status` semantics for "approval pending" should match D11; run `test_rbac.py` against the real module.
- P2: Ask role for specialists (we map them to `agent`: policy and own cases only); and whether the policy path should
  also scrub names and health details from answer text (tracked separately).
- Team: whether team leads should be scoped to their own team's customers in Next best action once batch ownership is settled.
- The team-lead outlier thresholds (D14) were tuned on synthetic data; recheck on real override rates.

---

## 10. Where things live

| Concern | Files |
|---|---|
| Roles, capabilities, customer scope | `ui/tiui/rbac.py`; `can` map in `ui/tiui/routers/session.py` |
| Decision state and "approval pending" | `ui/tiui/nba_state.py` |
| Role overviews | `ui/tiui/routers/overview.py`, `ui/web/src/pages/Overview.tsx` |
| Next best action | `ui/tiui/routers/next_action.py`, `ui/web/src/pages/NextAction.tsx` |
| Decision review, audit log | `ui/tiui/routers/oversight.py`, `ui/tiui/audit.py`, `ui/web/src/pages/DecisionReview.tsx`, `AuditLog.tsx` |
| Approvals, queue, case card | `ui/tiui/routers/nba.py`, `ui/web/src/pages/Approvals.tsx`, `Queue.tsx`, `DecisionCard.tsx` |
| Data freshness banner | `ui/tiui/routers/status.py` (`/freshness`), `DataWarning` in `ui/web/src/components/Shell.tsx` |
| Sign-in tabs | `ui/web/src/pages/SignIn.tsx`, `ui/tiui/routers/session.py` |
| Navigation and labels | `ui/web/src/components/Shell.tsx`, `ui/web/src/lib/labels.ts` |
| Sorting and search | `DataTable` in `ui/web/src/components/app.tsx` |
| Preview data, accounts, stand-ins | `ui/preview.py`, `ui/preview_data.py`, `ui/preview_backend.py` |
| Access tests | `tests_ui/test_rbac.py` |

---

## 11. Template

```
### Dn: <short title>

**Decision.** What we chose, in one or two sentences.
**Replaces.** Dm (or "nothing").
**Why.** The reasoning, including the data, rule or incident that drove it.
**Rejected.** The alternative(s) and why they lost.
**Consequence.** What this changes for users, security or later work.
**Where.** Files that implement it.
```
