# Text features: build notes (P4 → P3, interface I3)

An LLM reads each eligible note and call transcript and fills fixed fields; code checks every value. Output:
`gold.text_features`, one row per text, keyed by `source_id`, with `source_ts` as the point-in-time key.

Code: `src/trustissues/features/text_extract.py` (extract), `src/trustissues/features/text_eval.py` (score).
Tests: `tests/test_text_features.py`. Report: `reports/text_feature_eval.md`.

## 1. Run it

```bash
python -m trustissues.features.text_extract --scope labelled        # the 1,000 public labelled texts (~20 min)
python -m trustissues.features.text_eval                            # writes reports/text_feature_eval.md
python -m trustissues.features.text_extract --scope open_cases --limit 20000   # features P3 can use
python -m trustissues.features.text_extract --scope all             # every eligible text (~713k): see section 4
```

Re-running skips texts already extracted with the current prompt version, so a stopped run picks up where it left off.
Changing the prompt means changing `PROMPT_VERSION`; old rows stay, marked with their version.

## 2. Fields

| Column | Values | Note |
|---|---|---|
| `hardship_signal` (I3) | bool | `hardship_level` is `clear` or `possible` |
| `stated_delay_reason` (I3) | reduced_income, payroll_timing, forgot, refuses, bank_error, travel, dispute, other, NULL | Fixed rules after the model: a dispute wins; job loss / hours cut / EI → reduced_income; illness, disability, separation, rate reset, disaster → other. The public labels follow the same rules without exception. |
| `ptp_intent_strength` (I3) | strong ≥ 0.70, moderate 0.55–0.70, weak, none | From `ptp_intent_score` (0–1). The labels use 0.85 firm / 0.45 hedged. |
| `dispute_mentioned` (I3) | bool | plus `dispute_type` |
| `evidence_sentence` (I3) | text | Must be in the text word for word, or it is dropped and confidence is capped at 0.5. P3's decision card shows it. |
| `confidence`, `prompt_version` (I3) | | |
| extras | `source_type, hardship_level, hardship_reason, ptp_mentioned, ptp_amount, ptp_date, ptp_intent_score, dispute_type, cease_request, vulnerability, vulnerability_type, next_step, sentiment, model, extracted_at` | The interface check allows extra columns. Category lists are the release labels' own. |

## 3. Safety

- **Only eligible text.** Input is `gold.customer_timeline`, which holds only text that passed text_controls (redaction,
  retention, as-of date). It is scrubbed again before it is sent.
- **Point in time.** Use a feature only for decisions after its `source_ts`. P3 joins on `source_ts < ptp_created_ts`.
- **Every call is audited** (`gold.audit_log`, `kind = text_feature`, the IDs in the batch, the full prompt and reply).
- **Not a decision on its own.** Hardship and vulnerability signals route a case to a human (P3's `nba/hardship.py`);
  they never make the treatment harsher.
