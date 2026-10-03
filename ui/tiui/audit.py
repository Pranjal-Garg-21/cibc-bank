"""The UI's own access log and LLM audit store, in a small DuckDB file next to the warehouse (never the warehouse).

ui.access_log: one row per sign-in/out, refused request, opened case, Ask question, decision and export.
gold.audit_log in the same file: LLM calls made from the UI, written by trustissues.common.audit.AuditLog when
that module is installed. Append only: the API has no endpoint that updates or deletes either table.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import duckdb


class AccessLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._con: duckdb.DuckDBPyConnection | None = None

    def con(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._con = duckdb.connect(str(self.path))
            self._con.execute("CREATE SCHEMA IF NOT EXISTS ui")
            self._con.execute("""CREATE TABLE IF NOT EXISTS ui.access_log (ts TIMESTAMP, user_id VARCHAR,
                role VARCHAR, action VARCHAR, object_id VARCHAR, request_id VARCHAR, outcome VARCHAR)""")
        return self._con

    def record(self, *, action: str, outcome: str = "ok", user_id: str | None = None, role: str | None = None,
               object_id: str | None = None, request_id: str | None = None) -> None:
        with self._lock:
            self.con().execute("INSERT INTO ui.access_log VALUES (?, ?, ?, ?, ?, ?, ?)",
                               [datetime.now(timezone.utc).replace(tzinfo=None), user_id, role, action,
                                (object_id or "")[:200], request_id, outcome])

    def llm_audit(self):
        """An AuditLog writing LLM calls into this file, or None if common/audit.py is not installed."""
        try:
            from trustissues.common.audit import AuditLog
        except ImportError:
            return None
        return _LockedAudit(AuditLog(self.con()), self._lock)

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None


class _LockedAudit:
    def __init__(self, inner, lock):
        self.inner, self.lock = inner, lock

    def record(self, **kw):
        with self.lock:
            self.inner.record(**kw)
