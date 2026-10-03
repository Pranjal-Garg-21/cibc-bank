# Text feature evaluation

LLM text features (`src/trustissues/features/text_extract.py`, prompt `text_features_v1`) scored against the release's public labels (500 notes, 500 transcripts). Built by `python -m trustissues.features.text_eval`.

**Coverage:** 449 of 500 labelled notes and 472 of 500 labelled transcripts have features. The rest were not eligible to reach an LLM (empty text, `needs_review`, past retention) and get no text features.

## Gate: **PASS**

| check | needed | result |
|---|---:|---:|
| hardship_signal F1 | 0.80 | 0.92 |
| stated_delay_reason accuracy | 0.70 | 0.84 |
| dispute_mentioned F1 | 0.80 | 0.91 |
| ptp_mentioned F1 | 0.85 | 0.98 |

## Scores

| field | notes | transcripts | all | detail (all) |
|---|---:|---:|---:|---|
| hardship_signal F1 | 0.88 | 0.94 | 0.92 | n=921; precision 0.85, recall 1.00, 388 positive |
| hardship level (none/possible/clear) accuracy | 0.88 | 0.88 | 0.88 | n=921 |
| hardship_reason (where labelled) accuracy | 0.94 | 1.00 | 0.98 | n=313 |
| stated_delay_reason accuracy | 0.85 | 0.83 | 0.84 | n=921 |
| ptp_mentioned F1 | 0.96 | 0.99 | 0.98 | n=921; precision 0.96, recall 1.00, 550 positive |
| dispute_mentioned F1 | 0.76 | 0.93 | 0.91 | n=921; precision 0.83, recall 1.00, 69 positive |
| cease_request F1 | 0.47 | 0.23 | 0.30 | n=921; precision 0.18, recall 1.00, 9 positive |
| vulnerability F1 | 0.70 | 0.45 | 0.56 | n=921; precision 0.48, recall 0.68, 131 positive |
| next_step accuracy | 0.89 | 0.63 | 0.76 | n=921 |
| sentiment accuracy | 0.43 | 0.40 | 0.42 | n=921 |
| ptp intent mean abs error (0-1, lower is better) | 0.07 | 0.04 | 0.05 | n=550 |
| ptp_intent_strength band accuracy | 0.99 | 0.94 | 0.96 | n=550 |
| evidence sentence kept (verbatim) | 0.82 | 0.87 | 0.84 | n=921 |

## Stated reason for paying late: confusion

| label \ predicted | (none) | bank_error | dispute | forgot | other | payroll_timing | reduced_income | refuses | travel |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| (none) | 229 | 0 | 2 | 0 | 19 | 6 | 9 | 0 | 0 |
| bank_error | 0 | 16 | 1 | 0 | 3 | 0 | 0 | 0 | 0 |
| dispute | 0 | 0 | 68 | 0 | 0 | 0 | 0 | 0 | 0 |
| forgot | 1 | 0 | 1 | 67 | 7 | 0 | 1 | 0 | 0 |
| other | 25 | 0 | 3 | 0 | 145 | 1 | 18 | 0 | 0 |
| payroll_timing | 0 | 0 | 0 | 0 | 4 | 57 | 6 | 0 | 0 |
| reduced_income | 2 | 0 | 2 | 0 | 21 | 0 | 168 | 0 | 0 |
| refuses | 3 | 0 | 6 | 0 | 0 | 1 | 0 | 11 | 0 |
| travel | 0 | 0 | 0 | 1 | 0 | 1 | 1 | 0 | 15 |

## Hardship level: confusion

| label \ predicted | clear | none | possible |
|---|---:|---:|---:|
| clear | 310 | 0 | 0 |
| none | 21 | 464 | 48 |
| possible | 40 | 1 | 37 |

## How to read this

- `hardship_signal` is true for `clear` or `possible` hardship, as in the labels.
- `stated_delay_reason` follows fixed rules after the model answers: a dispute is the reason when one is raised; job loss, reduced hours and EI waiting are `reduced_income`; illness, disability, separation, rate reset and disaster are `other`. The public labels follow the same rules.
- `ptp_intent_strength` bands: strong >= 0.70, moderate 0.55-0.70, weak below (labels: 0.85 firm, 0.45 hedged).
- The labels are not perfect (e.g. "serious illness in family" labelled no hardship), so a few disagreements are the label's, not the model's.
