"""Text controls: which agent notes and call transcripts may be used downstream (timeline, RAG, LLM features).

    curated.agent_notes_text         note_id, eligible, exclusion_reason, text_masked, masked
    curated.call_transcripts_text    transcript_id, eligible, exclusion_reason, text_masked, masked

Eligibility (design §3 "Text controls"):
  notes        excluded if redaction_status = 'needs_review' or retention_until is before AS_OF_DATE
  transcripts  excluded unless both PII and PCI redaction were applied, or if the recording's retention
               (contact_history.recording_retention_until) is before AS_OF_DATE

DEFAULT, flagged for review: redaction_status = 'none' is treated as "no redaction was needed", not as
"unredacted". The upstream redaction cannot be trusted on its own, though: card numbers were found in 1,881
'none' notes and in 108 'auto_redacted' notes. So every eligible text also goes through our own masking, and
`masked` records that something was replaced. Masked: card numbers, SINs, phone numbers, emails, and a date
written after "DOB" / "date of birth" / "né(e) le". Names are kept: the timeline is shown only for a customer the
user is allowed to see, and the call opening ("May I speak with ...") needs them to make sense.
"""
from __future__ import annotations

import time

from trustissues.common import config
from trustissues.common.db import connect

# Order matters: the longest digit runs (cards) first, so a card number is never half-masked as a phone.
MASKS = [
    (r"\b(?:[0-9][ -]?){12,18}[0-9]\b", "[CARD]"),
    (r"\b[0-9]{3}[ -][0-9]{3}[ -][0-9]{3}\b", "[SIN]"),
    (r"\(?\b[2-9][0-9]{2}\)?[ .-]?[0-9]{3}[ .-]?[0-9]{4}\b", "[PHONE]"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL]"),
    (r"(?i)\b(dob|date of birth|ddn|n[ée]e? le)(\s*:?\s*)[0-9]{4}-[0-9]{2}-[0-9]{2}", "\\1\\2[DOB]"),
]


def mask_sql(expr: str) -> str:
    for pattern, repl in MASKS:
        expr = f"regexp_replace({expr}, '{pattern}', '{repl}', 'g')"
    return expr


def build(con) -> dict:
    as_of = config.AS_OF_DATE.isoformat()
    con.execute(f"""CREATE OR REPLACE TABLE curated.agent_notes_text AS
        SELECT note_id,
               exclusion_reason IS NULL AS eligible, exclusion_reason,
               CASE WHEN exclusion_reason IS NULL THEN text_masked END AS text_masked,
               exclusion_reason IS NULL AND text_masked IS DISTINCT FROM note_text AS masked
        FROM (SELECT note_id, note_text, {mask_sql('note_text')} AS text_masked,
                     CASE WHEN redaction_status = 'needs_review' THEN 'needs_review'
                          WHEN retention_until < DATE '{as_of}' THEN 'retention_expired' END AS exclusion_reason
              FROM curated.agent_notes)""")
    con.execute(f"""CREATE OR REPLACE TABLE curated.call_transcripts_text AS
        WITH t AS (
            SELECT j.transcript_id,
                   array_to_string(list_transform(
                       list_sort(from_json(j.content, '{{"turns": [{{"turn_index": "INTEGER", "speaker": "VARCHAR", "text": "VARCHAR"}}]}}').turns,
                                 'ASC'),
                       x -> x.speaker || ': ' || x.text), chr(10)) AS text
            FROM raw.transcript_json j)
        SELECT c.transcript_id,
               reason IS NULL AS eligible, reason AS exclusion_reason,
               CASE WHEN reason IS NULL THEN text_masked END AS text_masked,
               reason IS NULL AND text_masked IS DISTINCT FROM t.text AS masked
        FROM curated.call_transcripts c
        LEFT JOIN (SELECT transcript_id, text, {mask_sql('text')} AS text_masked FROM t) t USING (transcript_id)
        LEFT JOIN (SELECT contact_id, recording_retention_until FROM curated.contact_history WHERE contact_id IS NOT NULL
                   QUALIFY row_number() OVER (PARTITION BY contact_id) = 1) h USING (contact_id),
        LATERAL (SELECT CASE WHEN NOT coalesce(c.pii_redaction_applied, false) OR NOT coalesce(c.pci_redaction_applied, false)
                                  THEN 'redaction_not_applied'
                             WHEN h.recording_retention_until < DATE '{as_of}' THEN 'retention_expired'
                             WHEN t.text IS NULL THEN 'no_transcript_file' END AS reason)""")
    out = {}
    for t in ("agent_notes_text", "call_transcripts_text"):
        out[t] = dict(zip(["rows", "eligible", "masked"], con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE eligible), count(*) FILTER (WHERE masked) FROM curated.{t}").fetchone()))
    return out


def main() -> None:
    t0 = time.time()
    con = connect()
    print(build(con), f"{time.time() - t0:.0f}s")
    con.close()


if __name__ == "__main__":
    main()
