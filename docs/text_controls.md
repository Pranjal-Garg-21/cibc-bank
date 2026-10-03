# Text controls: what customer text may reach an LLM

Code: [`src/trustissues/factory/curate/text_controls.py`](../src/trustissues/factory/curate/text_controls.py) (the path the implementation plan gives it, §3.2). Tests: `tests/test_text_controls.py`. Settings: `text_controls:` in `config/settings.yaml`.

Every note and call transcript passes two gates before it is sent to a model (text features, customer answers):

1. **Eligibility**: is this text allowed to be used at all?
2. **Scrubbing**: remove personal identifiers from what is allowed.

```bash
python -m trustissues.factory.curate.text_controls     # (re)build the eligibility views and print what they do
```

## 1. Eligibility (SQL views, one source of truth)

`curated.note_text_eligibility` and `curated.transcript_text_eligibility` have one row per text with `eligible` and a `reason`. Cheap views: nothing is copied. Anyone who needs eligible text reads the view; nobody re-implements the rules.

| Source | A text is eligible only if |
|---|---|
| Note | `redaction_status` is in the allowed list, `retention_until` has not passed, it is dated on or before the as-of date, and the text is not empty |
| Transcript | PII **and** PCI redaction applied, the recording's retention date has not passed, and it is dated on or before the as-of date |

Transcripts have no retention column of their own, so the date comes from `contact_history.recording_retention_until` through `contact_id`. **An unknown retention date counts as not eligible** (the safe side). A retention date equal to the as-of date is still in date.

**Point in time.** The views use the as-of date (2026-09-28). For training, join on `note_ts` / `call_start_ts` and keep only text dated before each promise was created, so the model never sees the future.

### Result on the real data (as of 2026-09-28)

| Notes | Count | Share |
|---|---:|---:|
| Eligible | 688,046 | 90.5% |
| Past retention | 44,715 | 5.9% |
| Empty text | 23,704 | 3.1% |
| `needs_review` (a human has not looked) | 4,129 | 0.5% |

| Transcripts | Count | Share |
|---|---:|---:|
| Eligible | 24,918 | 99.7% |
| Recording past retention | 82 | 0.3% |

## 2. The `none` question, and what the data says

`redaction_status = none` is 94.5% of notes, and the design excluded it until its meaning was known. Excluding it would leave almost no text. The setting `note_redaction_statuses` (default `[auto_redacted, none]`) makes this a one-line choice; **it is a team decision, and worth asking the organisers**.

Measured on the real notes: the share containing a card-like number (a 13 to 19 digit run):

| `redaction_status` | Notes | With a card-like number |
|---|---:|---:|
| `needs_review` | 4,129 | 2,131 (**51.6%**) |
| `none` | 718,763 | 1,900 (0.26%) |
| `auto_redacted` | 37,702 | 108 (0.29%) |

- `none` is **no dirtier than `auto_redacted`**, which supports including it.
- **Neither is clean.** 108 notes marked `auto_redacted` still contain card numbers, so the flag cannot be trusted alone. This is a data-quality finding in its own right.
- `needs_review` is the dirty group, as its name says, and stays excluded.

## 3. The scrubber

`scrub(text)` replaces identifiers with placeholders and returns what it replaced, so the audit log can record it.

| Replaced with | What |
|---|---|
| `[EMAIL]` | email addresses |
| `[CARD]` / `[NUMBER]` | any 13 to 19 digit run in any grouping: `[CARD]` if it passes the Luhn check, `[NUMBER]` otherwise |
| `[ID]` | SIN-style `123-456-789` |
| `[PHONE]` | North American phone numbers, any separator, optional `+1` |
| `[POSTAL]` | Canadian postal codes |
| `[NUMBER]` | other digit runs of 9 or more |

Left alone: amounts (`$180`, `$1,234,567`), dates, and codes (`HP-LOAN-RED3`, `§4.2`). Tests cover both directions.

**Run on every eligible note** (688,046): 1,888 notes changed (1,693 `NUMBER`, 195 `CARD`), and **no run of 7 or more digits remains** in any scrubbed note.

**How the first version was wrong.** It only removed numbers that pass the Luhn check, but the data's card numbers are often grouped oddly (`450 843 2129 9978`) and fail it. Only part of them was removed and fragments such as `9978` were left. That version changed 204 notes; fixing it raised that to 1,888. The test file now includes the real shapes.

**Limits.** Names cannot be found by regex, so a note that names a person is not changed. The scrubber is a second safety net behind redaction, not a replacement for it.

## 4. Using it

```python
from trustissues.common.db import connect
from trustissues.factory.curate.text_controls import build_views, eligible_notes, scrub

con = connect()
build_views(con)                                   # once; re-run if the as-of date or the allowed statuses change
for note in eligible_notes(con, limit=10):         # eligible only, text already scrubbed
    note["note_text"], note["scrubbed"]            # scrubbed == {"CARD": 1} etc.
```

## 5. Open points

- **Ownership.** The implementation plan lists `text_controls.py` under P1 (curated layer) and P1's status notes show it as upcoming. It was built here by P4 at the planned path so there is one copy; P1 should adopt it rather than write another.
- **The `none` decision** (section 2).
- **Transcript text** is JSON in `raw.transcript_json`; the scrubber works on any string, but the transcript turns are not yet parsed and scrubbed.
- **The data-quality report** could carry the card-numbers-in-redacted-notes finding as a check.
