"""Which notes and call transcripts may be used downstream (timeline, RAG, LLM features), and the scrubber that runs on them first.

One file for both P1's and P4's versions (merge.md §1): P4's eligibility rules and scrubber are the single source;
P1's date-of-birth mask, transcript-turn parsing and materialised text tables were added to them.

Three parts, all needed before any customer text leaves this machine (design D16, governance):

1. Eligibility, as SQL views over the curated tables (one source of truth; nobody re-implements the rules):
     curated.note_text_eligibility        one row per note        -> eligible, reason
     curated.transcript_text_eligibility  one row per transcript  -> eligible, reason
   A text is eligible only if ALL hold:
     notes        redaction_status is in the allowed list (config text_controls.note_redaction_statuses),
                  retention_until has not passed, the note is dated on or before the as-of date, text not empty
     transcripts  PII and PCI redaction applied, the recording's retention date (contact_history.
                  recording_retention_until, via contact_id) has not passed, dated on or before the as-of date
   An unknown retention date is treated as NOT eligible (the safe side). `reason` says why a text was refused.
   Point in time: the views use the as-of date. For training, join on `note_ts` / `call_start_ts` and keep only
   text dated before each promise was created.

2. `scrub(text)`: a regex pass that replaces phone numbers, emails, card numbers, SIN-style numbers, a date written
   after "DOB" / "date of birth" / "DDN" / "né(e) le", Canadian postal codes and other long digit runs with
   placeholders. It is a second safety net behind redaction, not a replacement for it: names are not detectable by
   regex, so a note that names a person is not changed. The upstream redaction label cannot be trusted on its own
   (card numbers were found in `none` and in `auto_redacted` notes), so every eligible text is scrubbed.

3. The scrubbed text, materialised once so the timeline does not run a Python loop per build:
     curated.agent_notes_text         note_id, eligible, exclusion_reason, text_masked, masked, scrubbed
     curated.call_transcripts_text    transcript_id, eligible, exclusion_reason, text_masked, masked, scrubbed
   `exclusion_reason` is NULL for an eligible text, else the view's reason (plus `no_transcript_file` for a
   transcript whose JSON is missing). Transcript text is the JSON turns as `speaker: text` lines in turn order.
   `masked` says something was replaced, `scrubbed` what (JSON, e.g. {"CARD": 1}). gold.customer_timeline reads these.

    python -m trustissues.factory.curate.text_controls      # (re)build the views and tables and print what they do
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Iterator

from trustissues.common import config
from trustissues.common.db import connect

# ------------------------------------------------------------------ scrubber
_DOB = re.compile(r"(?i)\b(dob|date of birth|ddn|n[ée]e? le)(\s*:?\s*)\d{4}[-/]\d{2}[-/]\d{2}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_CARD = re.compile(r"(?<![\d$.])(?:\d[ -]?){12,18}\d(?![\d])")  # 13-19 digits in any grouping
_SIN = re.compile(r"(?<!\d)\d{3}[ -]\d{3}[ -]\d{3}(?!\d)")
_PHONE = re.compile(r"(?<![\d$])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-]?)\d{3}[\s.-]?\d{4}(?!\d)")
_POSTAL = re.compile(r"\b[ABCEGHJ-NPRSTVXY]\d[A-Z][ -]?\d[A-Z]\d\b", re.IGNORECASE)
_LONG = re.compile(r"(?<![\w$.,])\d{9,}(?![\w])")


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


@dataclass
class Scrubbed:
    text: str
    found: dict[str, int] = field(default_factory=dict)  # kind -> how many were replaced


def scrub(text: str | None) -> Scrubbed:
    """Replace personal identifiers with placeholders such as [PHONE]. Amounts, dates and codes are left alone."""
    if not text:
        return Scrubbed(text or "")
    found: dict[str, int] = {}

    def sub(pattern, label, s):
        def rep(m):
            kind = label(m.group(0)) if callable(label) else label
            found[kind] = found.get(kind, 0) + 1
            return f"[{kind}]"
        return pattern.sub(rep, s)

    def dob(m):  # keep the "DOB " prefix so the sentence still reads; only the date goes
        found["DOB"] = found.get("DOB", 0) + 1
        return f"{m.group(1)}{m.group(2)}[DOB]"

    # a 13-19 digit run is always removed: a valid card number is labelled CARD, any other grouping (the data has
    # numbers like `450 843 2129 9978`) NUMBER. Leaving it unchanged would leak the digits that fail the check.
    t = _DOB.sub(dob, text)
    t = sub(_EMAIL, "EMAIL", t)
    t = sub(_CARD, lambda x: "CARD" if luhn_ok(re.sub(r"\D", "", x)) else "NUMBER", t)
    t = sub(_SIN, "ID", t)
    t = sub(_PHONE, "PHONE", t)
    t = sub(_POSTAL, "POSTAL", t)
    t = sub(_LONG, "NUMBER", t)
    return Scrubbed(t, found)


# ------------------------------------------------------------------ eligibility views
def _statuses(statuses: list[str] | None) -> list[str]:
    return statuses if statuses is not None else config.settings().get("text_controls", {}).get(
        "note_redaction_statuses", ["auto_redacted"])


def build_views(con, as_of: date | None = None, statuses: list[str] | None = None) -> None:
    """(Re)create the two eligibility views. Cheap: nothing is copied."""
    d = (as_of or config.AS_OF_DATE).isoformat()
    allowed = ", ".join("'" + s.replace("'", "''") + "'" for s in _statuses(statuses))
    con.execute(f"""
        CREATE OR REPLACE VIEW curated.note_text_eligibility AS
        SELECT note_id, crm_customer_id, note_ts,
               CASE WHEN note_text IS NULL OR trim(note_text) = '' THEN 'empty_text'
                    WHEN redaction_status IS NULL OR redaction_status NOT IN ({allowed})
                         THEN 'redaction_status:' || coalesce(redaction_status, 'null')
                    WHEN retention_until IS NULL THEN 'no_retention_date'
                    WHEN retention_until < DATE '{d}' THEN 'past_retention'
                    WHEN CAST(note_ts AS DATE) > DATE '{d}' THEN 'after_as_of_date'
                    ELSE 'ok' END AS reason,
               reason = 'ok' AS eligible
        FROM curated.agent_notes""")
    # one contact_history row per contact_id, so a repeated contact_id cannot duplicate a transcript
    con.execute(f"""
        CREATE OR REPLACE VIEW curated.transcript_text_eligibility AS
        SELECT t.transcript_id, t.crm_customer_id, t.call_start_ts,
               CASE WHEN NOT coalesce(t.pii_redaction_applied, false) THEN 'pii_not_redacted'
                    WHEN NOT coalesce(t.pci_redaction_applied, false) THEN 'pci_not_redacted'
                    WHEN c.recording_retention_until IS NULL THEN 'no_retention_date'
                    WHEN c.recording_retention_until < DATE '{d}' THEN 'past_retention'
                    WHEN CAST(t.call_start_ts AS DATE) > DATE '{d}' THEN 'after_as_of_date'
                    ELSE 'ok' END AS reason,
               reason = 'ok' AS eligible
        FROM curated.call_transcripts t
        LEFT JOIN (SELECT contact_id, recording_retention_until FROM curated.contact_history
                   WHERE contact_id IS NOT NULL
                   QUALIFY row_number() OVER (PARTITION BY contact_id ORDER BY recording_retention_until) = 1) c
               USING (contact_id)""")


def summary(con) -> dict[str, list[tuple[str, int]]]:
    """How many texts each rule refuses, for both sources (governance report, README, tests)."""
    q = "SELECT reason, count(*) FROM {v} GROUP BY 1 ORDER BY 2 DESC"
    return {"notes": con.execute(q.format(v="curated.note_text_eligibility")).fetchall(),
            "transcripts": con.execute(q.format(v="curated.transcript_text_eligibility")).fetchall()}


def eligible_notes(con, *, limit: int | None = None, scrub_text: bool | None = None) -> Iterator[dict]:
    """Eligible notes with scrubbed text, ready to send to a model. Each dict also says what was scrubbed."""
    do_scrub = config.settings().get("text_controls", {}).get("scrub_pii", True) if scrub_text is None else scrub_text
    cur = con.execute(f"""
        SELECT n.note_id, n.crm_customer_id, n.note_ts, n.note_type, n.note_language, n.note_text
        FROM curated.agent_notes n JOIN curated.note_text_eligibility e USING (note_id)
        WHERE e.eligible {f'LIMIT {int(limit)}' if limit else ''}""")
    cols = [c[0] for c in cur.description]
    while rows := cur.fetchmany(5000):
        for r in rows:
            d = dict(zip(cols, r))
            s = scrub(d["note_text"]) if do_scrub else Scrubbed(d["note_text"])
            d["note_text"], d["scrubbed"] = s.text, s.found
            yield d


# ------------------------------------------------------------------ materialised text
TRANSCRIPT_TEXT_SQL = """
    SELECT j.transcript_id,
           array_to_string(list_transform(
               list_sort(from_json(j.content, '{"turns": [{"turn_index": "INTEGER", "speaker": "VARCHAR", "text": "VARCHAR"}]}').turns,
                         'ASC'),
               x -> x.speaker || ': ' || x.text), chr(10)) AS text
    FROM raw.transcript_json j"""


def _scrubbed_table(con, key: str, sql: str):
    """Run `sql` (key, text) and return a pyarrow table key, text_masked, masked, scrubbed with every text scrubbed."""
    import pyarrow as pa

    keys, texts, masked, found = [], [], [], []
    cur = con.execute(sql)
    while rows := cur.fetchmany(20000):
        for k, text in rows:
            s = scrub(text)
            keys.append(k)
            texts.append(s.text)
            masked.append(bool(s.found))
            found.append(json.dumps(s.found, sort_keys=True) if s.found else None)
    return pa.table({key: pa.array(keys, pa.string()), "text_masked": pa.array(texts, pa.string()),
                     "masked": pa.array(masked, pa.bool_()), "scrubbed": pa.array(found, pa.string())})


def build_text_tables(con) -> dict:
    """Materialise the eligible, scrubbed text of every note and transcript (part 3). Needs build_views first."""
    notes = _scrubbed_table(con, "note_id", """
        SELECT n.note_id, n.note_text FROM curated.agent_notes n
        JOIN curated.note_text_eligibility e USING (note_id) WHERE e.eligible""")
    con.register("_notes_scrubbed", notes)
    con.execute("""CREATE OR REPLACE TABLE curated.agent_notes_text AS
        SELECT e.note_id, e.eligible, CASE WHEN NOT e.eligible THEN e.reason END AS exclusion_reason,
               s.text_masked, coalesce(s.masked, false) AS masked, s.scrubbed
        FROM curated.note_text_eligibility e LEFT JOIN _notes_scrubbed s USING (note_id)""")
    con.unregister("_notes_scrubbed")

    con.execute(f"CREATE OR REPLACE TEMP TABLE _transcript_text AS {TRANSCRIPT_TEXT_SQL}")
    transcripts = _scrubbed_table(con, "transcript_id", """
        SELECT e.transcript_id, t.text FROM curated.transcript_text_eligibility e
        JOIN _transcript_text t USING (transcript_id) WHERE e.eligible AND t.text IS NOT NULL""")
    con.register("_transcripts_scrubbed", transcripts)
    con.execute("""CREATE OR REPLACE TABLE curated.call_transcripts_text AS
        SELECT e.transcript_id, e.eligible AND s.transcript_id IS NOT NULL AS eligible,
               CASE WHEN NOT e.eligible THEN e.reason WHEN s.transcript_id IS NULL THEN 'no_transcript_file' END
                   AS exclusion_reason,
               s.text_masked, coalesce(s.masked, false) AS masked, s.scrubbed
        FROM curated.transcript_text_eligibility e LEFT JOIN _transcripts_scrubbed s USING (transcript_id)""")
    con.unregister("_transcripts_scrubbed")
    con.execute("DROP TABLE _transcript_text")

    out = {}
    for t in ("agent_notes_text", "call_transcripts_text"):
        out[t] = dict(zip(["rows", "eligible", "masked"], con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE eligible), count(*) FILTER (WHERE masked) FROM curated.{t}").fetchone()))
    return out


def main() -> None:
    t0 = time.time()
    con = connect()
    build_views(con)
    print(f"as-of date {config.AS_OF_DATE}; allowed note redaction statuses: {_statuses(None)}")
    for name, rows in summary(con).items():
        total = sum(n for _, n in rows)
        print(f"\n{name}: {total:,}")
        for reason, n in rows:
            print(f"  {reason:28s} {n:>10,}  {n / total:6.1%}")
    print("\n", build_text_tables(con), f"{time.time() - t0:.0f}s")
    con.close()


if __name__ == "__main__":
    main()
