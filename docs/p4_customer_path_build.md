# Customer path: build notes (P4)

The customer path of Layer 2 (design: `p4_design_sections.md` §2, D1, D13–D17). Answers a question about one customer
from that customer's own history. Code: `src/trustissues/ask/rag/customer_path.py`. Tests: `tests/test_customer_path.py`.
Settings: `customer_path:` in `config/settings.yaml`.

## 1. Status

| Step | State |
|---|---|
| Access re-check (agent: assigned case; team lead: own team's case or an unassigned open case) | Done, tested, checked on real data |
| Fetch `gold.customer_timeline` by golden ID, point in time | Done, tested, checked on real data |
| Text filter: only items with eligible text, scrubbed again; later outcomes dropped for past dates | Done, tested |
| Token budget newest-first, asked-about case first (D15) | Done, tested |
| Answer with checked quotes and the customer `not_found` (shared `answer.py`) | Done, tested with a scripted stand-in. **Not yet run against the real model** (no key on this machine) |
| `coverage` line in the I6 response | Done |
| I6 entry `answer_customer(RagRequest)` | Done. P2's `rag_handler.py` still returns "not connected": one line to swap, see section 5 |

## 2. Run it

```bash
pytest tests/test_customer_path.py
python scripts/ask_customer.py "Summarise the most recent recorded call: what did the customer say and agree to?" --case CS-2026-614370
python scripts/ask_customer.py "..." --case CS-2026-614370 --show-sources        # every item the model read
python scripts/ask_customer.py "..." --case CS-2026-614370 --as-agent AG-0001     # access refused
python scripts/ask_customer.py "What did they promise?" --customer G-0200543 --as-of 2026-09-10
```

The CLI asks as the agent who holds the case on the as-of date (or as a team lead if nobody does). It needs
`OPENROUTER_API_KEY` and a writable warehouse (the audit log is written).

## 3. How it works

```
RagRequest {question, golden_customer_id, case_id?, user_role, user_id, as_of_date}
   1 access   can_view_customer(): SQL over case_assignment_history + case_customer (+ agents, collections_cases)
   2 fetch    gold.customer_timeline WHERE golden_customer_id = ? AND event_ts < as_of + 1 day
   3 filter   notes / transcripts: text present (= eligible under text_controls), scrub() again
              contacts / promises / payments / offers: their one-line summary
              as-of before the build date: promise status and offer outcome removed (recorded later)
   4 budget   newest first, the case's items before other cases', until token_budget (20K); then oldest first
   5 answer   answer.answer_question(system=customer prompt): quotes checked word for word, citations from
              the items quoted, not_found = "Not found in this customer's history."
RagResponse {answer, evidence[], citations[kind=note|transcript|contact|..., source_id, source_ts], confidence,
             not_found, coverage="Based on 17 items (notes, calls, ...) from 2026-08-29 to 2026-09-27; 17 on case ..."}
```

- **Why the structured events are included.** The design spoke of notes and transcripts. The dev answers need more:
  BQ-022 expects "asked to stop contact", which is a `contact_history` outcome (`cease request`). Contacts, promises,
  payments and offers are read as one-line summaries, so they cost little.
- **Each item is labelled** with its type, ID, date and time, case, channel and note type or call length, so "the most
  recent call on case X" is answerable. Items are numbered oldest first.
- **Refusal.** `CustomerAccessDenied` is a `PermissionError` with the same message whether or not the customer exists.
  The model is never called.
- **Audit.** Every answer is logged with `kind = rag_customer` and `prompt_version = rag_customer_v1`.

## 4. What the real data showed

| Finding | Consequence |
|---|---|
| Histories are small: median 11 events, 99th percentile 73, largest 149; the largest history is ~3,700 tokens with summaries. | The 20K budget is never reached today. Long context is the right call (D1); the budget is a safety net. |
| BQ-016 and BQ-022: the gold transcripts (5010384, 10650) are exactly the latest call on the named case. | Labelling each item with its case and time is what the question needs. |
| BQ-016's customer has a note 3 minutes after the call repeating it ("off work sick since Jul. promised 95 by the 14th"). | Good corroboration; the model may quote either. |
| **17% of open cases (14,862) have no assignment on 2026-09-28**, including test question BQ-021 (`CS-2026-619801`). | With "assigned agent only" nobody could ask about them. Team leads may read customers whose open case nobody holds. **Team decision, see below.** |
| Fetch for one customer takes 0.03 s warm (0.7 s on a cold database). | Latency is the model call. |

## 5. Open items

1. **Wire into Ask (P2).** In `ask/rag_handler.py`, replace the "not connected" line with
   `return customer_path.answer_customer(req, con=con, llm=llm)`, and treat `PermissionError` as a refusal
   (`service.py` catches only its own `AccessDenied`). P2's `RagRequest` already has `case_id`; this branch adds the same field.
2. **Access rule for team leads: confirm.** Today: own team's cases, plus any customer with an unassigned open case.
   P2's `check_case` currently lets a team lead see any case; the two should agree. Also decide which user the benchmark
   runner asks as.
3. **Run against the real model** on BQ-016 and BQ-022 (dev) and BQ-021 (test). Then tune the prompt.
4. **Point in time is partial.** The timeline only holds text eligible on the build's as-of date, so a past question
   cannot see a note that was eligible then but has expired since (safe side). Promise and offer outcomes are stripped
   for past dates; contact outcomes describe the contact itself and are kept.
5. **Depends on the main branch.** `gold.customer_timeline` (with `case_id` and `summary`) and `gold.case_customer`
   come from P1's work on `main`; the code also runs on the stub table shape (no case or summary).
6. **`confidence`** is still the model's own number, as on the policy path.
