"""Which notes and call transcripts may be shown to an LLM, and the scrubber that runs on them first.

Two parts, both needed before any customer text leaves this machine (design D16, governance):

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

2. `scrub(text)`: a regex pass that replaces phone numbers, emails, card numbers, SIN-style numbers,
   Canadian postal codes and other long digit runs with placeholders. It is a second safety net behind redaction,
   not a replacement for it: names are not detectable by regex, so a note that names a person is not changed.

    python -m trustissues.factory.curate.text_controls      # (re)build the views and print what they do
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterator

from trustissues.common import config
from trustissues.common.db import connect

# ------------------------------------------------------------------ scrubber
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

    # a 13-19 digit run is always removed: a valid card number is labelled CARD, any other grouping (the data has
    # numbers like `450 843 2129 9978`) NUMBER. Leaving it unchanged would leak the digits that fail the check.
    t = sub(_EMAIL, "EMAIL", text)
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
        LEFT JOIN curated.contact_history c USING (contact_id)""")


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


def main() -> None:
    con = connect()
    build_views(con)
    print(f"as-of date {config.AS_OF_DATE}; allowed note redaction statuses: {_statuses(None)}")
    for name, rows in summary(con).items():
        total = sum(n for _, n in rows)
        print(f"\n{name}: {total:,}")
        for reason, n in rows:
            print(f"  {reason:28s} {n:>10,}  {n / total:6.1%}")
    con.close()


if __name__ == "__main__":
    main()
