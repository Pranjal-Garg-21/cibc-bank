# Text controls: what customer text may reach an LLM

Code: [`src/trustissues/factory/curate/text_controls.py`](../src/trustissues/factory/curate/text_controls.py) (the path the implementation plan gives it, §3.2). Tests: `tests/test_text_controls.py`. Settings: `text_controls:` in `config/settings.yaml`.

Every note and call transcript passes two gates before it is used downstream (timeline, text features, customer answers):

1. **Eligibility**: is this text allowed to be used at all?
2. **Scrubbing**: remove personal identifiers from what is allowed.

The result is materialised once (section 4) as the text that `gold.customer_timeline` carries, so everyone downstream reads the same eligible, scrubbed text.

**One version since the P4 merge (4 Oct).** P1 (on `main`) and P4 each built this file. The merged file keeps P4's eligibility rules and scrubber as the single source and adds P1's three extras: the date-of-birth mask, transcript-turn parsing, and the materialised text tables that the timeline reads (`merge.md` §1).

```bash
python -m trustissues.factory.curate.text_controls     # views + text tables; prints what each rule refuses (pipeline stage text_controls)
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
| `[DOB]` | a date written after `DOB`, `date of birth`, `DDN`, `né(e) le` (the label is kept: `DOB [DOB]`). From P1's version |
| `[PHONE]` | North American phone numbers, any separator, optional `+1` |
| `[POSTAL]` | Canadian postal codes |
| `[NUMBER]` | other digit runs of 9 or more |

Left alone: amounts (`$180`, `$1,234,567`), dates, and codes (`HP-LOAN-RED3`, `§4.2`). Tests cover both directions.

**Run on every eligible note** (688,046): 1,888 notes changed (1,693 `NUMBER`, 195 `CARD`), and **no run of 7 or more digits remains** in any scrubbed note.

**How the first version was wrong.** It only removed numbers that pass the Luhn check, but the data's card numbers are often grouped oddly (`450 843 2129 9978`) and fail it. Only part of them was removed and fragments such as `9978` were left. That version changed 204 notes; fixing it raised that to 1,888. The test file now includes the real shapes.

**Limits.** Names cannot be found by regex, so a note that names a person is not changed. The scrubber is a second safety net behind redaction, not a replacement for it.

## 4. Materialised text (read by the timeline)

`build_text_tables(con)` (run by `main()`, so by the pipeline stage) writes:

| Table | Columns |
|---|---|
| `curated.agent_notes_text` | `note_id, eligible, exclusion_reason, text_masked, masked, scrubbed` |
| `curated.call_transcripts_text` | `transcript_id, eligible, exclusion_reason, text_masked, masked, scrubbed` |

- One row per note / transcript. `exclusion_reason` is NULL when eligible, otherwise the view's `reason`; a transcript whose JSON file is missing gets `no_transcript_file`.
- `text_masked` is `scrub()`'s output (eligible rows only), `masked` says something was replaced, `scrubbed` what, as JSON (`{"CARD": 1}`).
- Transcript text is `raw.transcript_json`'s turns as `speaker: text` lines in turn order (P1's SQL), then scrubbed.
- The scrub runs in Python once per build, not inside the view build, so the regexes are the same ones `scrub()` uses everywhere (DuckDB's regex engine has no look-behind, so P1's SQL port could not match P4's patterns exactly).
- `gold/views.py` `customer_timeline` joins these tables on `eligible`. Table names and columns are the ones P1's version had, plus `scrubbed`, so the timeline code did not change.

What changed for the timeline with the merge: blank notes are now excluded (`empty_text`), and notes with no retention date or dated after the as-of date are refused at this gate. Numbers from the rebuild are in section 6.

## 5. Using it

```python
from trustissues.common.db import connect
from trustissues.factory.curate.text_controls import build_views, eligible_notes, scrub

con = connect()
build_views(con)                                   # once; re-run if the as-of date or the allowed statuses change
for note in eligible_notes(con, limit=10):         # eligible only, text already scrubbed
    note["note_text"], note["scrubbed"]            # scrubbed == {"CARD": 1} etc.
```

## 6. Rebuild after the merge (4 Oct)

Ran `python -m trustissues.factory.pipeline --only text_controls`, then `--only views`, then `--only publish` (identity and gold unchanged, so not re-run). Publish: every blocking rule passes; the same two warnings as before (Q6, Q7).

| | P1's version (before) | Merged version |
|---|---:|---:|
| Eligible notes | 710,248 | 688,046 |
| Eligible transcripts | 24,918 | 24,918 (25,000 rows, no duplicates) |
| `text_controls` stage time | ~1 s (SQL) | 9 s (views + Python scrub of every eligible text) |
| Notes in `gold.customer_timeline` | 710,177 | 687,977 (−22,200: blank notes) |
| Transcripts in `gold.customer_timeline` | 24,914 | 24,914 |

What the scrub replaced: notes 1,848 `DOB`, 1,693 `NUMBER`, 195 `CARD` (3,736 notes changed); transcripts 5,484 `PHONE`, which are the bank's own call-back number in voicemail messages ("Please call us back at [PHONE]"), masked because a regex cannot tell it from a customer's. No eligible note has a run of 7 or more digits after scrubbing.

## 7. Open points

- **The `none` decision** (section 2): still a team decision; the default allows `none`.
- **The data-quality report** could carry the card-numbers-in-redacted-notes finding as a check.
