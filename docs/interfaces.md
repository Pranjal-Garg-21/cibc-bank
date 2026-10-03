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
| I3 | P4 → P3 | `gold.text_features` | Exactly the plan's columns. |
| I6 | P2 ↔ P4 | `RagRequest` / `RagResponse` / `Citation` | Python dataclasses, not a table. |
| — | P3 → P2 | `gold.nba_decisions` | Added to the Ask schema graph. |
| I7 | App → P3 | `gold.nba_overrides` | Exactly the plan's columns. |
| — | all | `gold.audit_log` | Written by `common/audit.py`. |

## Open questions for review

1. **`semantic_*` views (P1, P2):** one view per certified metric shape. There are 15 metrics in `metric_definitions` (roll rate, cure rate, promise kept/broken, RPC, PTP, cost per contact/RPC, collected amount, hardship, delinquency, write-off, average DPD, attempts per case, payroll delay). P2 should say which shapes it needs.
2. **`decisioning_customer` columns (P1, P3):** the list is a proposal. P3, say what you need. `fsa` and `age` are left out and only joined back in the fairness test.
3. **Consent columns (P1):** `consent_phone/sms/email/mail` are guessed names. Match them to the dictionary.
4. **`ptp_intent_strength` values (P4, P3):** `strong | moderate | weak | none` is a guess.
5. **`reason_code` list for overrides (P3):** still open in the P4 doc.
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
6. **`decision_card` JSON (P3, app):** shape is the `DecisionCard` dataclass (action, channel, risk score, top reasons, runner-up, blocked actions, evidence quotes, approval fields, summary).
7. **Fixture fixes:** `load_fixtures` refuses a real warehouse (only in-memory or a file named `*fixture*`). Fixture buckets now use the release labels (`1-30`, `31-60`, `61-90`), and the fixture decision card no longer claims email was blocked by consent when consent is `yes`.

Still open: confirm the history grain; P2's `semantic_*` views; P3's explicit feature allow-list (the outcome list alone does not prevent leakage).
