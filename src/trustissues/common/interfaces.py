"""Interface I6: Ask router <-> RAG. Plain dataclasses so P2 and P4 build against one shape.

in:  question, question_type, golden_customer_id?, user_role, as_of_date
out: answer, evidence[], citations[], confidence, not_found, coverage
Also: apply_stubs() creates the empty gold.* stub tables from contracts/interfaces/stubs.sql.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import REPO_ROOT

STUBS_SQL = REPO_ROOT / "contracts" / "interfaces" / "stubs.sql"


@dataclass
class RagRequest:
    question: str
    question_type: str  # rag_customer | rag_corpus
    user_role: str  # agent | team_lead | analyst
    as_of_date: date
    golden_customer_id: str | None = None  # required for rag_customer


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


def apply_stubs(con, path: Path = STUBS_SQL) -> None:
    """Create the interface stub tables. Safe to re-run (CREATE ... IF NOT EXISTS)."""
    con.execute(path.read_text(encoding="utf-8"))
