"""P4's text features (gold.text_features, interface I3) as hardship / vulnerability signals and evidence quotes.

Used two ways, never as a model feature:
- routing: a recent hardship or vulnerability signal read from the person's notes or calls flags the case for
  nba/hardship.py, the same as a hardship flag on the case. That can only make the treatment gentler (wait or a
  support call by a certified agent, never a reminder or collections call). A text signal is a model's reading, so
  its hardship level is at most `possible` (hardship.level_from_case: `clear` needs the customer's own statement
  recorded by an agent or a form); programmes that need `clear` are not offered on it alone.
- the decision card's evidence: the sentences behind the signals, quoted with their source and date.

What counts (config p3.text_signals): a row of the person's dated before the as-of date and within `lookback_days`,
with confidence >= `min_confidence` and an evidence sentence. text_extract keeps an evidence sentence only if it is
in the text word for word (otherwise it drops it and caps confidence at 0.5), so a signal always has a checkable quote.

Why not through features/sources.py: its default-deny check reads config/sensitivity.yaml, which labels the source
tables, and evidence_sentence comes from agent_notes.note_text (direct_pii there). Here the text has already passed
text_controls (eligibility + scrub) and P4's verbatim check, and it is only shown to the agent who owns the case.
The columns read are listed in COLUMNS; nothing else from the table is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from trustissues.common import config
from trustissues.common.interfaces import EvidenceQuote

MAX_QUOTES = 3
# I3 columns, plus P4's `vulnerability` extra when text_extract has created it (ensure_table)
COLUMNS = ("golden_customer_id", "source_id", "source_ts", "hardship_signal", "dispute_mentioned",
           "ptp_intent_strength", "evidence_sentence", "confidence")


@dataclass
class TextSignal:
    hardship: bool = False
    vulnerable: bool = False
    evidence: list[EvidenceQuote] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.hardship or self.vulnerable


def settings() -> dict:
    return {"lookback_days": 90, "min_confidence": 0.6, **config.settings()["p3"].get("text_signals", {})}


def _priority(hardship: bool, vulnerable: bool, dispute: bool, ptp: str | None) -> int:
    """Which quotes go on the card first: what flagged the case, then disputes, then firm promises, then the rest."""
    if hardship or vulnerable:
        return 0
    if dispute:
        return 1
    return {"strong": 2, "moderate": 2, "weak": 3}.get(ptp, 4)


def load(con, people: list[str], as_of: date) -> dict[str, TextSignal]:
    """person (golden_customer_id) -> their text signal. People with no qualifying text are left out."""
    have = {r[0] for r in con.execute("""SELECT column_name FROM information_schema.columns
                                         WHERE table_schema = 'gold' AND table_name = 'text_features'""").fetchall()}
    if not people or not set(COLUMNS) <= have:
        return {}
    import pyarrow as pa

    s = settings()
    vuln = "coalesce(vulnerability, false)" if "vulnerability" in have else "false"
    con.register("_text_people", pa.table({"person": pa.array(sorted(set(people)), pa.string())}))
    rows = con.execute(f"""
        SELECT golden_customer_id, source_id, CAST(source_ts AS VARCHAR), coalesce(hardship_signal, false),
               {vuln}, coalesce(dispute_mentioned, false), ptp_intent_strength, evidence_sentence
        FROM gold.text_features
        WHERE golden_customer_id IN (SELECT person FROM _text_people)
          AND source_ts < TIMESTAMP '{as_of}'
          AND source_ts >= TIMESTAMP '{as_of}' - INTERVAL {int(s['lookback_days'])} DAY
          AND confidence >= ? AND evidence_sentence IS NOT NULL AND trim(evidence_sentence) <> ''
        QUALIFY row_number() OVER (PARTITION BY source_id ORDER BY confidence DESC) = 1  -- one row per text
        ORDER BY golden_customer_id, source_ts DESC, source_id""", [float(s["min_confidence"])]).fetchall()
    con.unregister("_text_people")
    out: dict[str, TextSignal] = {}
    ranked: dict[str, list[tuple[int, int, EvidenceQuote]]] = {}
    for i, (person, sid, ts, hardship, vulnerable, dispute, ptp, quote) in enumerate(rows):
        sig = out.setdefault(person, TextSignal())
        sig.hardship |= bool(hardship)
        sig.vulnerable |= bool(vulnerable)
        ranked.setdefault(person, []).append((_priority(hardship, vulnerable, dispute, ptp), i,
                                              EvidenceQuote(sid, ts, quote)))
    for person, quotes in ranked.items():  # newest first within a priority (rows are newest first)
        out[person].evidence = [q for _, _, q in sorted(quotes, key=lambda t: t[:2])[:MAX_QUOTES]]
    return out
