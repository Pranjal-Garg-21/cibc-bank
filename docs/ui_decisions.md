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
11. [Integration with main (4 Oct)](#11-integration-with-main-4-oct-branch-integrate-ui-web)
12. [Template](#12-template)

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
own. That is four dashboards (and, until D37, four sign-in tabs; now one sign-in form).

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
| **Hardship specialist** | Protect customers in hardship or vulnerable situations; work the cases the queue gives them (D32) | Overview, My work queue, Next best action, Hardship approvals, Policy & case questions |
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
| Staff decide only recommendations assigned to them; agents approve nothing | `rbac` (`decide`: agents, specialists and team leads, D32) + backend `review.record` (assigned agent only) |
| Hardship specialists approve only hardship and vulnerability | backend `APPROVERS` + UI scope (cases outside remit are 404) |
| Only team leads approve high balances and the digital batch | `approve_batch` capability + backend |
| QA can change nothing and never opens a case | no `decide`/`approvals`/`case_view`/`next_action` capability |
| Approvals need a fresh password check (and MFA code when enabled) | `require_reauthentication` (existing control) |
| Nobody approves a recommendation assigned to themself (D32) | `routers/nba.py` (card flag and `POST /api/review`) |

**Why.** The risky actions in collections (contacting a customer in hardship, a large balance, a bulk send) are exactly the
ones where one person acting alone causes harm. The design forces a second person, with a *narrower* power than the first.

### D6: sign-in: one tab per role, a wrong tab fails like a wrong password (replaced by D37, 4 Oct)

**Decision (no longer in force).** The sign-in page has four tabs: Agent, Team lead, Hardship, QA. Choosing the wrong tab for your account returns
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
| Do hardship specialists get a work queue? | ~~No: they review and approve~~ **Yes, since 4 Oct (D32)** | Revisited as planned: on the real 28 Sep queue specialists are assigned 1,440 recommendations, 450 of them support calls. |
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

**4 Oct, from click-testing:** the Overview counts already left out recommendations the agent had overridden, but the
Team & approvals and Hardship approvals lists (`agent_view.lead_digest`) still showed them, and a lead could reject a case
the agent had already stopped (it then counted twice in decision review, as an override and as a rejection). Now an
overridden recommendation leaves those lists, the card hides Approve / Reject, and `nba/review.record` refuses an
approval or rejection on it ("overridden by its agent: there is nothing to approve or reject"). If the agent changes their
mind and accepts again, the approval is needed again. Tests: `tests/test_review.py`, `tests/test_agent_view.py`.

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
- Real SSO, off-host monitoring and an independent security review (see `ui_security_review.md`).

**Needs confirming with the owners.**
- P3: the queue assigns 1,027 recommendations on 28 Sep to 10 QA analysts, who by D5 never open a case; should the queue
  allocate to QA at all? (D32)
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
| Sign-in (one form, D37) | `ui/web/src/pages/SignIn.tsx`, `ui/tiui/routers/session.py` |
| Navigation and labels | `ui/web/src/components/Shell.tsx`, `ui/web/src/lib/labels.ts` |
| Sorting and search | `DataTable` in `ui/web/src/components/app.tsx` |
| Preview data, accounts, stand-ins | `ui/preview.py`, `ui/preview_data.py`, `ui/preview_backend.py` |
| Access tests | `tests_ui/test_rbac.py` |

---

## 11. Integration with `main` (4 Oct, branch `integrate/ui-web`)

The console was built on an older `main` (`ebfd6c5`) with stand-ins for P2 and P3. Merged onto `main` (`96699d1`), it
replaces the Streamlit app (`app/`, removed). Each screen was checked against the real modules and the real warehouse
(every GET endpoint, each of the five roles: no 5xx) and against what the Streamlit pages did.

### D31: Ask is wired like the benchmark (case resolver, history through the request's connection)

**Decision.** `/api/ask` passes `resolver=case_customer_resolver(con, AS_OF)` on every question and, with `UI_ASK_LLM=1`,
`rag=make_rag_handler(index, llm, con=con)` built per request; the model client and policy index are built once per process.
**Replaces.** The UI branch's `make_rag_handler(PolicyIndex.load(), llm)` built once, with no resolver.
**Why.** Without them every customer-call question answered "Customer lookups are unavailable: no case resolver is connected."
The handler must read history through the request's own connection (DuckDB refuses a second connection to a file this process
holds), so it cannot be cached across requests. Same wiring as the Streamlit Ask page and `run_benchmark.py`.
**Consequence.** An agent asking about a case in today's queue gets an answer from that case's notes and calls; another agent
is refused with the router's wording. Checked in the browser on 4 Oct with the model on (agent AG-5765, case CS-2026-465948:
8 items, notes, calls, contacts and promises, all on that case).
**Where.** `ui/tiui/routers/ask.py`; tests `tests_ui/test_integration.py::test_case_question_reaches_the_customer_path`.

### D32: hardship specialists work their own queue; team leads decide recommendations given to them

**Decision.** `hardship_specialist` gains `my_queue` and `decide`; `team_lead` gains `decide`. A specialist may open any
recommendation assigned to them (outside that, the support remit of D3 still applies). The card's "accept / override"
buttons need both the assignment and `decide`.
**Replaces.** D8's "no queue for specialists".
**Why.** The real queue assigns recommendations to every staff role that holds cases: on 28 Sep, 1,440 to 83 hardship
specialists (450 support calls, 990 waits) and 3,540 to 63 team leads. The Streamlit pages let both decide their own (the
specialist from their queue page, the lead from the card). Without this those recommendations could not be worked in the UI.
`nba/review.py` still checks every decision (only the assigned member of staff accepts or overrides).
Because a specialist or lead can now hold a recommendation that needs their own kind of approval, the UI refuses an approval
or rejection by the person the recommendation is assigned to (the card hides the buttons; the API answers 409). D5's second
person stays a second person; `nba/review.py` alone would allow it (it checks the approver's role, not who they are).
**Rejected.** Giving QA analysts a queue (1,027 recommendations are assigned to 10 of them): D5 says QA never opens a case;
that allocation is a question for P3's queue (whether QA should be allocated cases at all), recorded in §9.
**Where.** `ui/tiui/rbac.py`, `ui/tiui/routers/nba.py`; tests `test_rbac.py` (capability table, route matrix,
`test_nobody_approves_a_recommendation_assigned_to_themself`),
`test_integration.py::test_hardship_specialist_works_own_queue`.

### D33: the decision card shows the person, not just the case

**Decision.** `/api/cases/{id}` also returns `agent_view.customer_context` (all the person's cases and their hardship offers,
across identity resolution) and the card shows them, with the customer ID, feature snapshot, a "No approval needed" and a
"Hardship / vulnerable" badge, and a line when there is no quoted evidence.
**Why.** Parity with the Streamlit Decision Card: safeguards, the 7-day contact cap and hardship history are the person's, and
the agent needs to see the other cases to understand them. `golden_customer_id` is not a sensitive column; rows still pass
`_scrub`.
**Where.** `ui/tiui/routers/nba.py`, `ui/web/src/pages/DecisionCard.tsx`, `ui/preview_backend.py` (stand-in).

### D34: Ask examples come from the server, by role

**Decision.** `GET /api/ask/examples` returns the Streamlit page's examples for the asker's Ask role (each answers on the
4 Oct warehouse) and, for an agent, a question about a case in their own queue, preferring one with a recorded call. It also
says whether the model is on, a key is configured and the policy index is built; the page shows a notice for each gap.
Tables from metric, drivers and SQL answers are shown once: the CSV copy `ask()` writes into `answer` is dropped (its
`summary:` line is kept).
**Why.** The UI branch's fixed examples were not answerable for every role (an agent asking a portfolio count is refused).
**Where.** `ui/tiui/routers/ask.py`, `ui/web/src/pages/Ask.tsx`.

### D35: Ask's free text is masked like its tables; Ask only reads

**Decision.** Before an answer leaves the server, its text (`answer`, `notes`, the SQL and string parameters) goes through
`governance.masking.mask_text` (P4's `scrub()`: phones, emails, dates of birth, card and ID numbers), and on case answers
the name of the person behind the named case (`gold.case_customer` -> `gold.c360_customer.full_name`) is masked word by word
as the role masks a `full_name` column: agent `J***`, team lead `***`, analyst `[NAME]`. When a table is returned, the CSV
copy `ask()` writes into `answer` is never sent (it would bypass the column masking); only a `summary:` line is kept.
Missing masking code fails closed (`***`).
**Replaces.** Rows-only masking (`mask_rows`).
**Why.** Review of the merge (4 Oct): rag_customer and SQL answers are free text. Checked on the real warehouse with the
model on: an agent's call summary named the customer in full ("the customer (Jeanne Andersen) confirmed her identity");
`scrub()` cannot know names. Now "the customer (J*** A***)". Case IDs, amounts and dates are untouched.
**Also confirmed.** Ask opens the warehouse read-only (`warehouse(settings)`, `read_only=True`); the only writers are
`POST /api/review/{id}` and `POST /api/review-batch` (one request at a time, process-wide lock); identity decisions go to a
CSV and the UI's audit to `warehouse/ui_audit.duckdb`. `LLM()` reads the `llm:` block of `config/settings.yaml`
(`common/llm.py`), the same as the benchmark. Whether the model is on shows on the Ask page and on Data status.
**Checked.** One question of each kind per Ask role, model on (agent AG-1003, team lead AG-1399, analyst AG-1304): metric
(agent refused by role; lead and analyst get the certified table), policy (all three), own case (agent and their lead
answered, analyst refused by role), someone else's case and a missing case (identical: "Case not found or not visible.").
**Where.** `ui/tiui/routers/ask.py`, `ui/tiui/security.py` (`mask_text`, `mask_names`), `ui/tiui/routers/status.py`;
tests `test_api.py` (text, CSV, names), `test_integration.py` (identical refusals; with `RUN_LLM_TESTS=1`, a real case
answer has no phone, email, card, DOB or the customer's name).

### D36: a cleared call is marked complete with its outcome; done cases leave the queue

**Decision.** A new decision `completed` (a third kind in `gold.nba_overrides`, beside agent and approval): once a call
(`agent_call` or `support_call`) is `ready`, its assigned agent clicks **Mark as complete** and picks the call outcome
(`review.CALL_OUTCOMES`: reached with a promise, reached and paid, reached and hardship discussed, reached with no
commitment, call back requested, no answer, wrong number; stored in `reason_code`, required). A completed call, or a
`wait` its agent accepts, gets the new state `done` and leaves the day's queue (a **Done** filter brings it back);
nothing more can be decided on a completed call. `ready` is shown as **Cleared** ("cleared to carry out"), and a wait's
card says "No contact today" instead of "No approval needed". The state rule is one CASE in `nba/review.py`
(`state_sql`): blocked > overridden > waiting_agent > waiting_approval > done > ready; the overview's query
(`tiui/nba_state.py`) follows it and `tests_ui/test_nba_state.py` keeps the two equal.
**Replaces.** "Ready" as the last state, and two state orders: the overview put overridden before blocked and the
agent before the approval, `review.status` the opposite (a call needing approval showed "To approve" in the queue and
"Agent to decide" on the overview).
**Why.** Click-testing (4 Oct): a ready case still showed Accept / Override with nothing to say it had been done, and a
protective wait for a vulnerable customer read "Ready / No approval needed", which looked like a missed safeguard. The
flow staff expect is accept, then approve, then make the call and close it. The agent goes first because they see the
case before anyone signs it off.
**Rejected.** Removing "Ready" (it is the only state for "every sign-off is in", used by the queue, overview and Ask);
"complete" for letters and reminders (the system or the digital batch sends them, not the agent); deleting done rows
(agents, QA and Change decision need them).
**Consequence.** Override rate is unchanged: completion is not an agent decision. `review.summary` adds
`call_outcomes`. Ask's case facts say "done: the call has been made (outcome)". `gold.nba_overrides` keeps its columns.
**Where.** `src/trustissues/nba/review.py`, `src/trustissues/ask/case_facts.py`, `ui/tiui/nba_state.py`,
`ui/tiui/routers/nba.py`, `ui/tiui/routers/overview.py`, `ui/preview_backend.py`, `ui/web/src/pages/DecisionCard.tsx`,
`ui/web/src/pages/Queue.tsx`, `ui/web/src/lib/labels.ts`; tests `tests/test_review.py`, `tests_ui/test_nba_state.py`,
`tests_ui/test_api.py`.

## 11b. Click-test fixes (4 Oct, evening)

Fourteen findings from click-testing the console with the real warehouse, fixed together. Measured on the 28 Sep
queue (86,059 recommendations, 86,051 people) unless a test is named.

### D37: one sign-in form; the role comes from the staff record

**Decision.** The sign-in page has one form for everyone: staff ID and password (and a verification code where MFA is
on). The login request carries no role; the server refuses any request that sends one (422, `extra="forbid"`), reads
the role from `curated.agents.role` after the password check, and the app goes to `/`, which opens that role's first
screen (its Overview). Any role in `rbac.ROLES` may sign in; an unknown staff ID, a wrong password, an inactive account
or a role with no screens all fail with the same message.
**Replaces.** D6 ("one tab per role, a wrong tab fails like a wrong password"). With no tab there is no wrong tab; the
rule that a failed sign-in never says whether the staff ID exists stays.
**Why.** Staff had to know which of four tabs their account belonged to, and a wrong tab looked like a wrong password.
The tab added nothing the server did not already know, and the browser should never be the source of a role.
**Rejected.** Keeping the tabs but ignoring them (misleading); a role dropdown filled from the server (D6's reason
still holds: nothing about staff before sign-in).
**Consequence.** QA analysts sign in like everyone else (they could before only on the QA tab). `rbac.PORTALS` and
`RoleSpec.portal` are gone; `rbac.AGENT_ROLES` names the roles whose passwords a team lead sets. After sign-in the app
always lands on the Overview, not the page that was open when the session ended (it may be another person's session).
**Where.** `ui/tiui/routers/session.py`, `ui/tiui/rbac.py`, `ui/tiui/deps.py`, `ui/web/src/pages/SignIn.tsx`,
`ui/preview_data.py`, `ui/preview.py`; tests `tests_ui/test_rbac.py`
(`test_one_sign_in_form_and_the_role_comes_from_the_warehouse`), `tests_ui/test_team.py`,
`tests_ui/test_banking_security.py` (`test_role_is_never_taken_from_the_browser`), `tests_ui/test_shared_demo_access.py`;
docs `ui/README.md`, `docs/teammate_setup.md`.

### D38: the daily batch asks for the password every time

**Decision.** Approving the daily batch sends the team lead's password (and code, where MFA is on) in the approval
request itself; the server checks it there (`deps.check_credentials`, the same check, rate limit and lock as
re-authentication, logged as `confirm_batch`). A re-authentication made for another action, however recent, does not
count.
**Replaces.** The batch used `require_reauthentication`, valid for 300 s (`deps.py`), so a second batch approval inside
that window went through without a prompt.
**Why.** One click signs off every automated reminder of the day (34,154 on 28 Sep); it should be confirmed on purpose
each time.
**Rejected.** Shortening the window (still skippable); consuming the re-authentication after the batch (a single-case
approval just before would still cover it).
**Consequence.** The confirmation dialog has a password field; a wrong password keeps the dialog open with the error.
Single-case approvals and account changes keep the 300 s re-authentication.
**Where.** `ui/tiui/routers/nba.py` (`BatchIn`, `approve_batch`), `ui/tiui/deps.py`, `ui/tiui/routers/session.py`,
`ui/web/src/pages/Approvals.tsx` (`BatchSection`); tests `tests_ui/test_api.py`
(`test_batch_needs_the_password_every_time`), `tests_ui/test_rbac.py`.

### D39: nothing is done twice

**Decision.** An action already done is not clickable again, and the server refuses it too. On the decision card:
once the agent accepts, **Accept** turns grey ("Accepted"), the current decision is shown, and **Override** is the only
way to change it; once overridden, **Override** is grey and accepting again is the way back. Once a case is approved or
rejected, **Approve** and **Reject** are both grey ("Approval: Approved (final)"). The batch button greys out at 0.
`nba.review.record` refuses an approval decision when one exists (approvals are final) and the agent decision that is
already current.
**Replaces.** Buttons stayed live after use, and the latest-row-wins rule let an approver flip approved/rejected.
**Why.** Click-testing: staff could not tell whether a click had registered, and a second click wrote a second row.
**Rejected.** Disabling in the browser only (a second tab or a script would still write twice).
**Consequence.** The history keeps one approval per case. An agent can still change their mind (accepted to overridden
and back), which re-opens the approval if one is needed (D11 unchanged).
**Where.** `src/trustissues/nba/review.py` (`record`), `ui/preview_backend.py`, `ui/web/src/pages/DecisionCard.tsx`;
tests `tests/test_review.py` (`test_nothing_is_decided_twice`), `tests_ui/test_rbac.py`
(`test_an_approval_is_final`, `test_agent_decides_own_cases_only_and_approves_nothing`).

### D40: overridden cases are archived, with who overrode them and why

**Decision.** A case its agent overrides leaves the active lists: the agent's queue hides it like a Done case (the
**Overridden** filter brings it back), and the team lead's approvals keep it out of "Waiting for approval" but list it
under **Overridden (n)** with the agent's name and staff ID, the reason in words (`review.REASON_CODES`), the approval it
would have needed, and when. The decision card of an overridden case opens with "Overridden by <name> (<staff ID>)",
the reason and the time, for every role that can open it; the decision history shows names and reason labels, not codes.
A hardship specialist's archive is limited to their remit.
**Replaces.** Overridden cases stayed in the agent's open list; a team lead saw no reason and no name (the history
showed `customer_already_paid`).
**Why.** A team lead reviewing a case that will no longer be carried out needs to know who stopped it and why.
**Consequence.** `agent_view.lead_digest` returns `overridden` (`agent_view.overridden`); `/api/cases/{id}` returns
`override`. Ask says the reason in words too ("overridden by the assigned agent (payment or arrangement already in
place)").
**Where.** `src/trustissues/nba/agent_view.py`, `ui/tiui/routers/nba.py`, `src/trustissues/ask/case_facts.py`,
`ui/web/src/pages/Approvals.tsx`, `Queue.tsx`, `DecisionCard.tsx`; tests `tests/test_agent_view.py`
(`test_lead_digest_lists_overridden_cases_with_who_and_why`), `tests_ui/test_rbac.py`
(`test_the_lead_sees_overridden_cases_with_who_and_why`).

### D41: every customer and every case can be found; live state everywhere

**Decision.** (a) Next best action lists every customer the role may see, 50 a page with Previous / Next and the total
(team lead 86,051; hardship specialist 13,522; agent AG-5944 271). (b) The agent's queue lists every case assigned to
them, not one row per person: `agent_queue` no longer drops a person's other cases, marks the one contacted today
(`todays_contact`), and the queue's own search covers every case (customer, case, recommendation, treatment, status,
summary), including done and overridden ones, whatever filter is on. (c) Next best action's Timing pillar shows the live
state ("Cleared · Approved (hardship)", "Waiting for hardship approval", "Rejected at approval") from
`gold.nba_overrides`, and the card header says "Approved: Hardship" once approved.
**Replaces.** `LIMIT 20` in `/api/next-action/customers`; `drop_duplicates("customer")` in `agent_view.agent_queue`; a
table filter that searched only the visible columns of the visible rows (the customer ID was not a column, and done
cases were filtered out first); the static `requires_approval` flag read as "Needs approval" after approval.
**Why.** Click-testing: the NBA tab showed 20 customers; an agent could not find some of their own customers; an
approved case still said it needed approval.
**Rejected.** A bigger LIMIT (still a cut-off); server-side search for the queue (271 rows at most per agent today).
**Consequence.** `/api/next-action/customers` takes `page`, `page_size` (10-200) and returns `total`; each
recommendation of `/api/next-action/customers/{id}` carries `status`. On the 28 Sep queue no agent has two cases of one
person, so (b) changes no counts today; it matters once identity merges more people.
**Where.** `ui/tiui/routers/next_action.py`, `src/trustissues/nba/agent_view.py`, `ui/preview_backend.py`,
`ui/web/src/pages/NextAction.tsx`, `Queue.tsx`, `DecisionCard.tsx`; tests `tests/test_agent_view.py`.

### D42: only the numbers each role acts on

**Decision.** Each screen keeps the figures its role uses to decide what to do next; queue figures always count the
whole queue, never the filtered rows. Removed:

| Screen | Removed | Kept |
|---|---|---|
| Agent overview | Recommendations today (a total), Your override rate (7 days), Today's treatments and Channels bars | Waiting for your decision, Cleared to carry out, Done today, Up next |
| My work queue | Recommendations (repeated in the list title), Waiting for approval (the agent cannot act on it) | Waiting for your decision, Cleared to carry out, Done today |
| Team lead overview | "Agents still to decide: across the bank" (now "Your agents still to decide", the lead's team only), "Where today's recommendations stand" (bank-wide bars), the batch in the by-type bars | Case approvals waiting, Automated reminders waiting, Your agents still to decide, Team override rate, approvals by type, Your team |
| Team & approvals | The six bank-wide state figures, the "Override review" panel (duplicated Decision review) | The batch, Waiting for approval (with type filters), Overridden |
| Hardship specialist overview | Vulnerability and Hardship tiles (the total split again), Support calls today (bank-wide), "Why a support call is due", "Programmes on the cards" | Waiting for your approval, Waiting for your decision and Cleared to carry out (their own queue), Next to review |
| QA overview | Recommendation mix today | Override rate, Safeguard overrides, Failed data rules, Refused requests, Overrides by reason, Risk score bands |

"Waiting for your decision" in the queue was checked by clicking the tile and the filter: it stays at the full count
(10 for DEMO-A1 in the preview, 87 for AG-5944) because it counts the whole queue.
**Why.** Click-testing: agents saw bank-wide counts they could do nothing about, and the same total in three places.
**Where.** `ui/tiui/routers/overview.py`, `ui/web/src/pages/Queue.tsx`, `Approvals.tsx`, `Overview.tsx` (the tile row
has one column per tile, so three tiles leave no empty cell).

### D43: names staff understand; header and MFA wording

**Decision.** Labels only, the stored codes stay: `push_reminder` reads **App notification** and channel `push`
**Mobile app notification** (web labels, overview, card text from `nba/explain.py`, Ask); `digital_batch` reads
**Automated reminders (daily batch)**, explained where it appears: "Reminders sent automatically by SMS, email or app
notification. No agent is involved, so a team lead approves the whole day's batch at once, and can pull out any case
first." The header shows the CIBC mark and **CIBC Collections** (page title too); the DEMO ENVIRONMENT tag is gone; with
MFA off in a demo the badge reads "Demo access · MFA enabled in deployment". Card text also names the runner-up action in
words ("vs $0.09 for an SMS reminder", not `sms_reminder`).
**Why.** "Push reminder" and "Digital batch" meant nothing to staff; "MFA disabled" read as a security fault.
**Consequence.** Decision cards already in `gold.nba_decisions` keep their old sentence ("Send an app push reminder",
runner-up codes) until the queue is next written (`make nightly`); labels around them change now. The CIBC mark is
drawn inline in `Wordmark.tsx` as a stand-in; swap in the bank's official logo file when one is approved.
**Where.** `ui/web/src/lib/labels.ts`, `ui/tiui/routers/overview.py`, `src/trustissues/nba/explain.py`,
`src/trustissues/ask/case_facts.py`, `ui/web/src/components/Wordmark.tsx`, `Shell.tsx`, `SecurityPanel.tsx`,
`ui/web/src/pages/SignIn.tsx`, `ui/web/index.html`; test `tests/test_ask_case_facts.py`.

### D44: approvals and decision review update live

**Decision.** Team & approvals, Decision review, the work queue, the overview and a customer's next best action refetch
every 20 s while the tab is visible and whenever the window regains focus (`live` in `ui/web/src/api/client.ts`). Any
decision, approval or batch approval in the same browser invalidates every screen it can change (`DECISION_QUERIES`).
Automatic refreshes send `X-TI-Background: 1`, and the server does not count them as activity, so an open screen never
keeps an idle session alive; only the person's own clicks and keys do (the idle lock's keep-alive).
**Replaces.** These screens fetched once (the overview every 60 s, which also reset the idle timer); decisions made in
another session appeared only after a manual reload.
**Rejected.** Server push (websockets): more moving parts for a 20 s need.
**Where.** `ui/web/src/api/client.ts`, `ui/tiui/deps.py` (`session_of`), `Approvals.tsx`, `DecisionReview.tsx`,
`Queue.tsx`, `Overview.tsx`, `NextAction.tsx`, `DecisionCard.tsx`; tests `tests_ui/test_security.py`
(`test_background_refresh_does_not_keep_an_idle_session_alive`), `ui/web/src/api/client.test.ts`.

---

## 12. Template

```
### Dn: <short title>

**Decision.** What we chose, in one or two sentences.
**Replaces.** Dm (or "nothing").
**Why.** The reasoning, including the data, rule or incident that drove it.
**Rejected.** The alternative(s) and why they lost.
**Consequence.** What this changes for users, security or later work.
**Where.** Files that implement it.
```
