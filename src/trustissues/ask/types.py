"""Shared Ask types: who is asking and what comes back. One shape for the app and the benchmark."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

ROLES = ("agent", "team_lead", "analyst")


@dataclass(frozen=True)
class UserContext:
    user_id: str
    role: str  # agent | team_lead | analyst

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"Unknown role: {self.role}")


@dataclass
class AnswerCard:
    question: str
    answer: str
    path: str  # refused | metric | sql | rag_customer | rag_corpus | cannot_answer
    as_of_date: date
    refused: bool = False
    refusal_reason: str | None = None
    metric_id: str | None = None
    sql: str | None = None  # executed SQL (certified template or labelled generated SQL)
    parameters: dict = field(default_factory=dict)  # bound values for a certified template
    certified: bool = False
    columns: tuple[str, ...] = ()
    rows: tuple[tuple, ...] = ()
    sources: list[str] = field(default_factory=list)  # tables, or policy / note / transcript ids
    notes: list[str] = field(default_factory=list)  # caveats shown with the answer
