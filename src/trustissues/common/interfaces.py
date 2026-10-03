"""Interface I6: Ask router <-> RAG. Plain dataclasses so P2 and P4 build against one shape.

in:  question, question_type, golden_customer_id?, case_id?, user_role, user_id, as_of_date
out: answer, evidence[], citations[], confidence, not_found, coverage
Also: apply_stubs(con) creates the empty gold.* stub tables from contracts/interfaces/stubs.sql and
checks any table that already exists against them. DecisionCard is the JSON shape stored in
gold.nba_decisions.decision_card.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .config import REPO_ROOT

STUBS_SQL = REPO_ROOT / "contracts" / "interfaces" / "stubs.sql"


@dataclass
class RagRequest:
    question: str
    question_type: str  # rag_customer | rag_corpus
    user_role: str  # agent | team_lead | analyst
    user_id: str  # agent id / lead id: the router authorises, RAG re-checks the customer against this user
    as_of_date: date
    golden_customer_id: str | None = None  # required for rag_customer
    case_id: str | None = None  # set when the question names a case (rag_customer): that case's items are read first


@dataclass
class Citation:
    kind: str  # policy | note | transcript
    source_id: str  # doc id, or note/transcript id
    version: str | None = None  # policy documents
    section: str | None = None  # policy documents
    source_ts: str | None = None  # notes and transcripts (date)


@dataclass
class RagResponse:
    answer: str | None
    evidence: list[str] = field(default_factory=list)  # quoted sentences
    citations: list[Citation] = field(default_factory=list)
    confidence: float | None = None
    not_found: bool = False  # sources do not contain the answer
    coverage: str | None = None  # e.g. "notes and calls from 2024-03-01 to 2026-09-28"


# Columns of gold.promises_enriched that are outcomes: labels / evaluation only, never feature inputs.
PROMISE_OUTCOME_COLUMNS = frozenset({
    "ptp_status", "status_ts", "amount_paid_against", "paid_date", "broken_reason_agent_coded", "label_broken",
})


@dataclass
class BlockedAction:
    action: str
    rule: str  # which hard rule removed it, e.g. cease_contact, consent, contact_limit


@dataclass
class Reason:
    feature: str
    text: str
    contribution: float  # SHAP value


@dataclass
class EvidenceQuote:
    source_id: str  # note or transcript id
    source_ts: str
    quote: str  # evidence sentence behind a text signal


@dataclass
class HardshipOption:
    """A hardship programme the case is eligible for on the as-of date (hardship_programs), cited to its policy."""
    program_id: str
    version: str
    name: str
    policy_section: str | None  # section of POL-COLL-004
    approver_level: str | None  # agent | hardship_specialist | team_lead


@dataclass
class DecisionCard:
    """JSON stored in gold.nba_decisions.decision_card. The app renders exactly these fields."""
    recommended_action: str
    channel: str | None
    risk_score: float
    top_reasons: list[Reason] = field(default_factory=list)  # SHAP top 3
    runner_up_action: str | None = None
    why_beat_runner_up: str | None = None
    blocked_actions: list[BlockedAction] = field(default_factory=list)
    evidence: list[EvidenceQuote] = field(default_factory=list)
    requires_approval: bool = False
    approval_reason: str | None = None  # hardship | vulnerability | high_balance | digital_batch
    summary: str = ""  # template text (optionally reworded, fact-checked)
    # Hardship and vulnerability (nba/hardship.py). support_reason says why a certified agent should call today:
    # offer_programme | plan_ending | follow_up | vulnerable_contact (None: no support call due).
    support_reason: str | None = None
    hardship_options: list[HardshipOption] = field(default_factory=list)  # eligible programmes, offer these first
    contact_window: str | None = None  # calls and texts: when they may be made that day, e.g. "09:00–18:00 customer-local"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class StubMismatch(Exception):
    """An existing gold table does not match the agreed stub shape."""


def _shape(con, tables=None) -> dict[tuple[str, str], str]:
    q = "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema='gold'"
    return {(t, c): d for t, c, d in con.execute(q).fetchall() if tables is None or t in tables}


def apply_stubs(con, path: Path = STUBS_SQL) -> None:
    """Create the stub tables. Safe on a fresh database.

    CREATE TABLE IF NOT EXISTS never alters a table that already exists, so a table left over from an older
    version of stubs.sql would pass silently. To catch that, the expected shape is built in a scratch
    in-memory database and every expected (table, column, type) must be present in `con`. Extra columns in
    `con` are allowed (a real table may carry more than the contract), missing or retyped ones are not.
    """
    import duckdb

    sql = path.read_text(encoding="utf-8")
    scratch = duckdb.connect(":memory:")
    scratch.execute(sql)
    expected = _shape(scratch)
    scratch.close()
    existing = _shape(con, {t for t, _ in expected})
    con.execute(sql)
    actual = _shape(con, {t for t, _ in expected})
    problems = [f"{t}.{c}: expected {d}, " + (f"found {actual[(t, c)]}" if (t, c) in actual else "missing")
                for (t, c), d in sorted(expected.items()) if actual.get((t, c)) != d]
    if problems and existing:
        raise StubMismatch("existing gold tables differ from stubs.sql:\n  " + "\n  ".join(problems))
