# Interface stubs I1-I7: for team review

Proposed shapes, taken from `implementation_plan.md` §7.1 and the `p1`–`p4` docs. The DDL is `contracts/interfaces/stubs.sql` (empty `gold.*` tables). I6 is code, in `src/trustissues/common/interfaces.py`. Everyone can build against these before the real gold build lands; P1 replaces each stub with a real table or view **of the same shape**. `apply_stubs(con)` creates them; `tests/test_interface_stubs.py` guards them.

**Fixtures:** `from trustissues.common.fixtures import load_fixtures` loads a few consistent FAKE rows into every stub (3 customers, 4 promises, a timeline, a decision card), so each owner can build before real gold lands.

**Rule:** changing a column is an interface change. Agree it in review, then edit `stubs.sql`.

| ID | From → To | Object | Notes |
|---|---|---|---|
| I1 | P1 → P3 | `gold.decisioning_customer` | Allow-list. No protected attributes, PII, geo, age, FSA or accent. |
| I1 | P1 → P3 | `gold.promises_enriched` | One row per promise. `ptp_created_ts` is the point-in-time key. `label_broken` is defined once here. |
| I1 | P1 → P3 | `gold.customer_safeguards` | Rules and hardship routing only, never model inputs. |
| I2 | P1 → P2 | `gold.bucket_transitions`, `gold.c360_customer`, `gold.c360_account`, `gold.customer_xref` | Inputs to the Ask schema graph. |
| I2 | P1 → P2 | `gold.semantic_*` | **Not stubbed**, see open question 1. |
| — | P1 → P4 | `gold.customer_timeline` | Unnumbered in the plan. Redacted, eligible text only. |
| I3 | P4 → P3 | `gold.text_features` | The plan's columns plus P4's extras (`text_extract.EXTRA_COLUMNS`, e.g. `vulnerability`). Written by `features/text_extract.py`; P3 reads it in `nba/text_signals.py` (hardship / vulnerability routing and card evidence, never a model feature). |
| I6 | P2 ↔ P4 | `RagRequest` / `RagResponse` / `Citation` | Python dataclasses, not a table. |
| — | P3 → P2 | `gold.nba_decisions` | Added to the Ask schema graph. |
| I7 | App → P3 | `gold.nba_overrides` | Exactly the plan's columns. |
| — | all | `gold.audit_log` | Written by `common/audit.py`. |

## Open questions for review

1. **`semantic_*` views (P1, P2):** one view per certified metric shape. There are 15 metrics in `metric_definitions` (roll rate, cure rate, promise kept/broken, RPC, PTP, cost per contact/RPC, collected amount, hardship, delinquency, write-off, average DPD, attempts per case, payroll delay). P2 should say which shapes it needs.
2. **`decisioning_customer` columns (P1, P3):** the list is a proposal. P3, say what you need. `fsa` and `age` are left out and only joined back in the fairness test.
3. **Consent columns (P1):** `consent_phone/sms/email/mail` are guessed names. Match them to the dictionary.
4. **`ptp_intent_strength` values (P4, P3):** `strong | moderate | weak | none` is a guess.
5. **`reason_code` list for overrides (P3):** settled in `nba/review.py` (`REASON_CODES`): `customer_already_paid`, `customer_contacted_today`, `hardship_disclosed`, `vulnerability_concern`, `dispute_raised`, `customer_requested_channel`, `contact_details_wrong`, `card_facts_wrong`, `too_much_contact`, `other`. The same list covers a team lead's rejections. `decision` also takes `approved` / `rejected` for approvals; the columns are unchanged.
6. **`label_broken` (P1, P3):** is `partially_kept` broken? The plan defers to the metric definition.
7. **`bucket_transitions` columns (P1, P2):** marked TBC in the P1 doc.
8. **Timeline interface number:** the plan never gave it one.

**Working defaults now in place (2026-10-03), to confirm:** questions 3, 6 and 7, and the `decisioning_customer` history grain, have defaults built and published; see [`consumer_views.md`](consumer_views.md) §1 (rows K, A, J, B–E). Question 1 (`semantic_*`) still needs P2.

## Changes after Codex review (proposals, to confirm)

1. **RAG identity (P2, P4):** `RagRequest` now has `user_id`. The router authorises; RAG re-checks the customer against that user.
2. **Outcome leakage (P3):** `promises_enriched` columns are split. Creation-time columns may feed features. Outcome columns (`ptp_status`, `status_ts`, `amount_paid_against`, `paid_date`, `broken_reason_agent_coded`, `label_broken`) are labels and evaluation only. `PROMISE_OUTCOME_COLUMNS` in `interfaces.py` holds the list. P3 should still keep an explicit feature allow-list.
3. **Row grain (P1, P3):** `decisioning_customer` is now a history table, one row per `(golden_customer_id, valid_from)`. `valid_from` is when values became true; `available_from` is when we could have known them, and is the point-in-time join key. `feature_ts` is gone. Confirm the grain.
4. **`apply_stubs`:** now raises `StubMismatch` if an existing table is missing a stub column or has a different type. Extra columns are allowed.
5. **Tests:** the exact table set, history columns, outcome-column ordering, I6 dataclass fields, the mismatch check and the fixtures are covered. Still not done: types of every column, and checking the protected and PII lists against the generated `sensitivity.yaml`, which is not on `main` yet.
6. **`decision_card` JSON (P3, app):** shape is the `DecisionCard` dataclass (action, channel, risk score, top reasons, runner-up, blocked actions, evidence quotes, approval fields, summary). **Added 3 Oct (P3), optional:** `support_reason` (why a certified agent should call today: `offer_programme`, `plan_ending`, `follow_up`, `vulnerable_contact`) and `hardship_options` (eligible hardship programmes: `program_id`, `version`, `name`, `policy_section`, `approver_level`). Also `contact_window` (calls and texts: when they may be made that day, customer-local). Older cards without them stay valid. `gold.nba_decisions.action` gains the value `support_call` (a certified agent offering help to a hardship or vulnerable customer); the columns are unchanged.
7. **Fixture fixes:** `load_fixtures` refuses a real warehouse (only in-memory or a file named `*fixture*`). Fixture buckets now use the release labels (`1-30`, `31-60`, `61-90`), and the fixture decision card no longer claims email was blocked by consent when consent is `yes`.

Still open: confirm the history grain; P2's `semantic_*` views are built and published since 4 Oct (`docs/semantic_views.md`); P3's explicit feature allow-list (the outcome list alone does not prevent leakage).

## Case to call (P2 ↔ P4): "summarise the most recent call on case X"

Covers BQ-016, BQ-021 and BQ-022 (and any unseen question of that shape). Agreed split: **P2 decides who may ask; P4 finds and summarises the call.**

**1. P2 resolves and authorises (done, `ask/access.py`).** A case ID in the question is resolved to its owner: `CaseResolver(case_id) -> CaseOwner(golden_customer_id, assigned_agent_id) | None`, read from `gold.semantic_cases` (`connection_resolver`, the Ask dev database) or, on the published warehouse, from `gold.case_customer` plus the agent assigned on the as-of date in `curated.case_assignment_history` (`case_customer_resolver(con, as_of)`, P2 merge 4 Oct; it reuses P4's `resolve_case` / `case_assignee`). An agent may ask only about a case assigned to them; team leads may ask about any case; analysts may not use this path. A missing case and a case the user may not see get the same wording, so the answer never reveals whether a case exists. Only then does P2 call the RAG handler.

**2. The request (I6 gains one field).** `RagRequest.case_id: str | None = None`, set by P2 whenever the question names a case. For this path the request carries `question_type='rag_customer'`, `golden_customer_id` (already authorised), `case_id`, `user_role`, `user_id`, `as_of_date`. RAG re-checks that the customer is allowed for `user_id` (the existing I6 rule) but does not need to resolve the case.

**3. What P4 returns.**
- **Scope:** the call is chosen from calls whose `call_transcripts.case_id` equals the request's `case_id`. A customer's whole timeline is not enough: "the most recent call" means the most recent for that case.
- **Which call:** latest `call_start_ts` that is on or before `as_of_date` and eligible (redaction applied, consent and retention rules). If no call qualifies, return `not_found=True` with a `coverage` line that says so, for example "no recorded calls on this case up to 2026-09-28". Never fall back to another case's call.
- **Citations:** `Citation(kind='transcript', source_id=<transcript_id>, source_ts=<call_start_ts date>)`. The gold sources for the dev questions are transcript IDs (`transcript 5010384`).
- **Answer content:** what the customer said and agreed to: stated reason, any promise (amount and date), requests such as stopping contact, and the agreed next step. Every quote must appear word for word in the transcript.
- **Text shown to users** goes through `governance/masking.py` (`mask_text`) before display; P2 applies it, so P4 does not need to. Since the P2 merge `mask_text` is P4's `text_controls.scrub()` (the single scrubber), so text reads the same here as in the timeline. `ask()` itself does not mask; the app's Ask page (`app/pages/1_Ask.py`) applies `mask_answer_card` by role to every card it shows, and the customer path already scrubs every item before the model sees it.

**4. Wiring (done 4 Oct, P2 merge).** `make_rag_handler(index, llm, con=...)` in `src/trustissues/ask/rag_handler.py` sends `rag_corpus` to `ask/rag/answer.answer_policy` and `rag_customer` to `ask/rag/customer_path.answer_customer` with the open warehouse connection; without `con` it returns `not_found` ("customer history is not connected"). When P4's re-check refuses (`CustomerAccessDenied`), the handler raises P2's `AccessDenied` with the router's wording (`access.NOT_VISIBLE`), so a team lead asking about a case outside their team gets the same answer as for a case that does not exist (P4's own message says "Customer …", P2's says "Case …"; passing it through would have told them the case exists). A model outage on either path is `not_found` with the reason in `coverage`. Tests: `tests/test_ask_rag_handler.py`, end to end through `ask()` on P4's customer-path fixture.

**Scope 3 is built (decided 4 Oct: P2's spec).** With `case_id` set, `customer_path.fetch_history` reads only that case's timeline items (filtered in SQL), so another case's call can never reach the model. Before this, P4's path read the named case first and then the customer's other items. No items on the case: `not_found` with no model call, coverage "No history on case X for this customer on or before <date>." Items but no call: the coverage line adds "no recorded calls on this case up to <date>". On the 4 Oct build every transcript, note, contact, promise and offer in `gold.customer_timeline` carries a `case_id`; payments do not (1,107,864 rows), so a case-scoped answer does not see payments. Without a case the whole history is read, as before.

**Tests P4 can run against fixtures:** an agent asking about an unassigned case never reaches RAG (`tests/test_ask_service.py::test_customer_rag_checks_assignment`); the handler receives `case_id` and `golden_customer_id` (same test).
