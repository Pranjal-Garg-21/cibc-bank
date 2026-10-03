"""Sessions, CSRF tokens, rate limits and column masking (docs/ui_design.md §9).

Everything here is in memory: one process serves the demo. A production deployment moves SessionStore and
RateLimiter to a shared store; nothing else changes.
"""
from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from trustissues.common import config

SESSION_COOKIE = "ti_session"


@dataclass
class Session:
    sid: str
    user_id: str
    role: str
    csrf: str
    created: float
    last_seen: float


class SessionStore:
    def __init__(self, idle_seconds: int, max_seconds: int, clock=time.monotonic):
        self.idle, self.max, self.clock = idle_seconds, max_seconds, clock
        self._s: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, user_id: str, role: str) -> Session:
        now = self.clock()
        s = Session(secrets.token_urlsafe(32), user_id, role, secrets.token_urlsafe(32), now, now)
        with self._lock:
            self._s[s.sid] = s
        return s

    def get(self, sid: str | None, touch: bool = True) -> Session | None:
        if not sid:
            return None
        now = self.clock()
        with self._lock:
            s = self._s.get(sid)
            if s is None:
                return None
            if now - s.last_seen > self.idle or now - s.created > self.max:
                del self._s[sid]
                return None
            if touch:
                s.last_seen = now
            return s

    def delete(self, sid: str | None) -> None:
        with self._lock:
            self._s.pop(sid or "", None)


def csrf_ok(session: Session, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(session.csrf, token)


@dataclass
class Limit:
    count: int
    seconds: int


@dataclass
class RateLimiter:
    """Sliding-window limits per (bucket, key). Returns seconds to wait, or 0 if the call may go ahead."""
    limits: dict[str, list[Limit]] = field(default_factory=lambda: {
        "ask": [Limit(10, 60), Limit(200, 86400)],
        "write": [Limit(60, 60)],
        "login": [Limit(5, 60)],
    })
    clock: callable = time.monotonic

    def __post_init__(self):
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str) -> int:
        now = self.clock()
        rules = self.limits.get(bucket, [])
        longest = max((r.seconds for r in rules), default=0)
        with self._lock:
            q = self._hits[(bucket, key)]
            while q and now - q[0] > longest:
                q.popleft()
            for r in rules:
                recent = [t for t in q if now - t <= r.seconds]
                if len(recent) >= r.count:
                    return max(1, int(r.seconds - (now - recent[0])) + 1)
            q.append(now)
            return 0


# --- masking --------------------------------------------------------------------------------------------------

SENSITIVE_LEVELS = {"direct_pii", "quasi_identifier", "protected"}
MASK = "***"


@lru_cache(maxsize=1)
def sensitive_columns() -> frozenset[str]:
    """Column names that config/sensitivity.yaml marks sensitive in any table (proxies count as protected)."""
    with open(config.CONFIG_DIR / "sensitivity.yaml", encoding="utf-8") as f:
        s = yaml.safe_load(f)
    names = {c.split(".")[-1].lower() for c in (s.get("protected_columns") or []) + (s.get("proxy_columns") or [])}
    for cols in (s.get("tables") or {}).values():
        names |= {c.lower() for c, level in (cols or {}).items() if level in SENSITIVE_LEVELS}
    return frozenset(names)


def _policy(columns, role: str, table: str | None) -> list[tuple[str, str]]:
    """(column, action) with action show | partial | mask | drop.

    Uses P2's governance.masking (role is an Ask role: agent | team_lead | analyst) when it is installed. Without
    it, every column that sensitivity.yaml marks sensitive is masked. Any error fails closed (mask everything).
    """
    try:
        from trustissues.governance import masking  # P2's branch
    except ImportError:
        return [(c, "mask" if c.lower() in sensitive_columns() else "show") for c in columns]
    try:
        return [(p.column, p.action) for p in masking.policy_for(list(columns), role, table)]
    except Exception:
        return [(c, "mask") for c in columns]


def mask_rows(columns, rows, role: str, table: str | None = None) -> tuple[list[str], list[list]]:
    """Apply the role's masking to a result set. Dropped columns disappear; masked values become ***."""
    try:
        from trustissues.governance.masking import partial
    except ImportError:
        partial = lambda _c, _v: MASK  # noqa: E731
    pol = _policy(columns, role, table)
    keep = [i for i, (_, a) in enumerate(pol) if a != "drop"]
    out = []
    for r in rows:
        row = []
        for i in keep:
            col, action, v = pol[i][0], pol[i][1], r[i]
            row.append(v if action == "show" or v is None else partial(col, v) if action == "partial" else MASK)
        out.append(row)
    return [pol[i][0] for i in keep], out


def mask_record(record: dict, role: str, table: str | None = None) -> dict:
    cols, rows = mask_rows(list(record), [list(record.values())], role, table)
    return dict(zip(cols, rows[0]))


def mask_phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return f"•••• {digits[-4:]}" if len(digits) >= 4 else (MASK if value else value)


_CSV_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    """A cell value that a spreadsheet will not run as a formula."""
    s = "" if value is None else str(value)
    s = s.replace("\r", " ").replace("\n", " ")
    return "'" + s if s.startswith(_CSV_FORMULA) else s


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_text(s: str | None, limit: int = 500) -> str:
    return _CONTROL.sub(" ", s or "").strip()[:limit]
