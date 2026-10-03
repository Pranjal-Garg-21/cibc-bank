# Text feature evaluation

LLM text features (`src/trustissues/features/text_extract.py`, prompt `text_features_v1`) scored against the release's public labels (500 notes, 500 transcripts). Built by `python -m trustissues.features.text_eval`.

**Coverage:** 429 of 500 labelled notes and 56 of 500 labelled transcripts have features. The rest were not eligible to reach an LLM (empty text, `needs_review`, past retention) and get no text features.

## Gate: **PASS**

| check | needed | result |
|---|---:|---:|
| hardship_signal F1 | 0.80 | 0.89 |
| stated_delay_reason accuracy | 0.70 | 0.86 |
| dispute_mentioned F1 | 0.80 | 0.83 |
| ptp_mentioned F1 | 0.85 | 0.97 |

## Scores

| field | notes | transcripts | all | detail (all) |
|---|---:|---:|---:|---|
| hardship_signal F1 | 0.88 | 0.95 | 0.89 | n=485; precision 0.81, recall 0.99, 179 positive |
| hardship level (none/possible/clear) accuracy | 0.88 | 0.88 | 0.88 | n=485 |
| hardship_reason (where labelled) accuracy | 0.94 | 1.00 | 0.96 | n=141 |
| stated_delay_reason accuracy | 0.85 | 0.91 | 0.86 | n=485 |
| ptp_mentioned F1 | 0.96 | 1.00 | 0.97 | n=485; precision 0.93, recall 1.00, 229 positive |
| dispute_mentioned F1 | 0.76 | 1.00 | 0.83 | n=485; precision 0.71, recall 1.00, 12 positive |
| cease_request F1 | 0.47 | 0.50 | 0.48 | n=485; precision 0.31, recall 1.00, 5 positive |
| vulnerability F1 | 0.69 | 0.17 | 0.61 | n=485; precision 0.49, recall 0.83, 60 positive |
| next_step accuracy | 0.90 | 0.50 | 0.85 | n=485 |
| sentiment accuracy | 0.44 | 0.39 | 0.44 | n=485 |
| ptp intent mean abs error (0-1, lower is better) | 0.07 | 0.03 | 0.06 | n=229 |
| ptp_intent_strength band accuracy | 0.99 | 0.98 | 0.99 | n=229 |
| evidence sentence kept (verbatim) | 0.82 | 0.91 | 0.83 | n=485 |

## Stated reason for paying late: confusion

| label \ predicted | (none) | bank_error | dispute | forgot | other | payroll_timing | reduced_income | refuses | travel |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| (none) | 164 | 0 | 2 | 0 | 13 | 2 | 7 | 0 | 0 |
| bank_error | 0 | 9 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| dispute | 0 | 0 | 11 | 0 | 0 | 0 | 0 | 0 | 0 |
| forgot | 0 | 0 | 1 | 33 | 2 | 0 | 0 | 0 | 0 |
| other | 11 | 0 | 1 | 0 | 69 | 0 | 8 | 0 | 0 |
| payroll_timing | 0 | 0 | 0 | 0 | 0 | 33 | 4 | 0 | 0 |
| reduced_income | 2 | 0 | 2 | 0 | 13 | 0 | 81 | 0 | 0 |
| refuses | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 |
| travel | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 |

## Hardship level: confusion

| label \ predicted | clear | none | possible |
|---|---:|---:|---:|
| clear | 138 | 0 | 0 |
| none | 13 | 264 | 29 |
| possible | 17 | 1 | 23 |

## How to read this

- `hardship_signal` is true for `clear` or `possible` hardship, as in the labels.
- `stated_delay_reason` follows fixed rules after the model answers: a dispute is the reason when one is raised; job loss, reduced hours and EI waiting are `reduced_income`; illness, disability, separation, rate reset and disaster are `other`. The public labels follow the same rules.
- `ptp_intent_strength` bands: strong >= 0.70, moderate 0.55-0.70, weak below (labels: 0.85 firm, 0.45 hedged).
- The labels are not perfect (e.g. "serious illness in family" labelled no hardship), so a few disagreements are the label's, not the model's.
