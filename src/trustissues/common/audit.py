"""Append-only audit log: every LLM call and every recommendation (design D20, governance).

One row per call in gold.audit_log with the full prompt, the sources used, the answer, the model and the
prompt version. The log holds customer text, so it is as restricted as the data it came from.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .interfaces import apply_stubs

COLUMNS = ("ts", "user_role", "user_id", "kind", "input_text", "sources", "prompt", "answer", "model", "prompt_version")


class AuditLog:
    """Writes to gold.audit_log on the given DuckDB connection. Creates the stub table if it is missing."""

    def __init__(self, con):
        self.con = con
        exists = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='gold' AND table_name='audit_log'"
        ).fetchone()[0]
        if not exists:
            apply_stubs(con)

    def record(self, *, kind: str, input_text: str, prompt: str, answer: str | None, model: str,
               prompt_version: str, sources: list[str] | None = None, user_role: str | None = None,
               user_id: str | None = None) -> None:
        self.con.execute(
            f"INSERT INTO gold.audit_log ({', '.join(COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [datetime.now(timezone.utc).replace(tzinfo=None), user_role, user_id, kind, input_text,
             json.dumps(sources or [], ensure_ascii=False), prompt, answer, model, prompt_version],
        )
