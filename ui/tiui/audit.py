"""The UI's own access log and LLM audit store, in a small DuckDB file next to the warehouse (never the warehouse).

ui.access_log: one row per sign-in/out, refused request, opened case, Ask question, decision and export.
gold.audit_log in the same file: LLM calls made from the UI, written by trustissues.common.audit.AuditLog when
that module is installed. Append only: the API has no endpoint that updates or deletes either table.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
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

    def search(self, *, hours: int = 24, user: str = "", action: str = "", outcome: str = "", text: str = "",
               limit: int = 200) -> tuple[list[dict], dict]:
        """Newest-first events plus counts for the same window. Parameters are bound, never interpolated.
        outcome: '' (any), 'ok', 'issues' (anything but ok) or an exact outcome. QA's audit screen only."""
        where, params = ["ts >= ?"], [datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)]
        if user:
            where.append("user_id ILIKE ?"); params.append(f"%{user}%")
        if action:
            where.append("action = ?"); params.append(action)
        if outcome == "issues":
            where.append("outcome <> 'ok'")
        elif outcome:
            where.append("outcome = ?"); params.append(outcome)
        if text:
            where.append("(object_id ILIKE ? OR user_id ILIKE ? OR action ILIKE ? OR request_id ILIKE ?)")
            params += [f"%{text}%"] * 4
        clause = " AND ".join(where)
        with self._lock:
            con = self.con()
            rows = con.execute(f"""SELECT ts, user_id, role, action, object_id, outcome, request_id FROM ui.access_log
                                   WHERE {clause} ORDER BY ts DESC LIMIT ?""", [*params, limit]).fetchall()
            total, issues = con.execute(f"""SELECT count(*), count(*) FILTER (WHERE outcome <> 'ok') FROM ui.access_log
                                            WHERE {clause}""", params).fetchone()
            actions = [r[0] for r in con.execute("SELECT DISTINCT action FROM ui.access_log ORDER BY 1").fetchall()]
            outcomes = [r[0] for r in con.execute("SELECT DISTINCT outcome FROM ui.access_log ORDER BY 1").fetchall()]
        keys = ("at", "user_id", "role", "action", "object_id", "outcome", "request_id")
        out = [dict(zip(keys, (r[0].replace(tzinfo=timezone.utc).isoformat(), *r[1:]))) for r in rows]
        return out, {"total": total, "issues": issues, "actions": actions, "outcomes": outcomes}

    def issues_since(self, hours: int = 24) -> int:
        """Refused, locked-out or otherwise not-ok events in the window (a headline number, no detail)."""
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        with self._lock:
            return self.con().execute("SELECT count(*) FROM ui.access_log WHERE ts >= ? AND outcome <> 'ok'",
                                      [since]).fetchone()[0]

    def recent_security(self, user_id: str) -> list[dict]:
        """Own authentication events only; no customer object ids, cookies, credentials or other users' events."""
        with self._lock:
            rows = self.con().execute("""SELECT ts, action, outcome FROM ui.access_log
                WHERE (user_id = ? OR (action = 'sign_in' AND object_id = ?))
                AND action IN ('sign_in', 'sign_out', 'reauthenticate', 'sign_out_others')
                ORDER BY ts DESC LIMIT 10""", [user_id, user_id]).fetchall()
        return [{"at": r[0].replace(tzinfo=timezone.utc).isoformat(), "action": r[1], "outcome": r[2]} for r in rows]

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
