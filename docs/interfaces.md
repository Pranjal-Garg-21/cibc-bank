# Interface stubs I1-I7: for team review

Proposed shapes, taken from `implementation_plan.md` §7.1 and the `p1`–`p4` docs. The DDL is `contracts/interfaces/stubs.sql` (empty `gold.*` tables). I6 is code, in `src/trustissues/common/interfaces.py`. Everyone can build against these before the real gold build lands; P1 replaces each stub with a real table or view **of the same shape**. `apply_stubs(con)` creates them; `tests/test_interface_stubs.py` guards them.

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
