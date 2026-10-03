"""The one place that opens connections and resolves who is asking (docs/ui_design.md §3.1, §9.1).

No long-lived warehouse connection: each request opens one, uses it and closes it, so the nightly rebuild is never
locked out by an idle UI. Requests that touch the warehouse are serialised by a process-wide lock.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

import duckdb
from fastapi import Request

from trustissues.common import config

from .errors import ApiError
from .security import SESSION_COOKIE, Session

_WAREHOUSE_LOCK = threading.Lock()

# Staff roles (agents.role) -> Ask / masking roles (ask.types.ROLES). Open question Q3 in the design note.
ASK_ROLE = {"agent": "agent", "senior_agent": "agent", "team_lead": "team_lead",
            "hardship_specialist": "team_lead", "qa_analyst": "analyst"}
SUPERVISORS = ("team_lead", "hardship_specialist", "qa_analyst")


class WarehouseBusy(ApiError):
    def __init__(self):
        super().__init__(503, "warehouse_busy", "Data is being refreshed. Try again in a minute.")


def _is_lock_error(e: Exception) -> bool:
    text = str(e).lower()
    return "lock" in text or "being used by another process" in text or "conflicting" in text


@contextmanager
def warehouse(settings, write: bool = False):
    """A connection for one request. Read-only unless the request writes through a backend function."""
    with _WAREHOUSE_LOCK:
        try:
            con = duckdb.connect(str(settings.warehouse), read_only=not write)
        except (duckdb.IOException, duckdb.ConnectionException) as e:
            if _is_lock_error(e):
                raise WarehouseBusy() from e
            raise
        try:
            yield con
        finally:
            con.close()


@dataclass
class User:
    agent_id: str
    role: str            # staff role, as in agents.role
    team_id: str | None
    certified: bool
    status: str
    display_name: str | None = None

    @property
    def ask_role(self) -> str:
        return ASK_ROLE.get(self.role, "agent")

    @property
    def is_supervisor(self) -> bool:
        return self.role in SUPERVISORS

    def as_staff(self) -> dict:
        """The dict shape nba.agent_view and nba.review expect."""
        return {"agent_id": self.agent_id, "role": self.role, "team_id": self.team_id,
                "certified": self.certified, "status": self.status}


def lookup_staff(con, agent_id: str) -> User | None:
    """Read straight from curated.agents (the same source nba.agent_view.staff uses), so sign-in works on main."""
    row = con.execute("""SELECT agent_id, role, team_id, coalesce(vulnerable_customer_certified, false), status,
                                agent_display_name
                         FROM curated.agents WHERE agent_id = ?""", [agent_id]).fetchone()
    return User(*row) if row else None


def session_of(request: Request) -> Session | None:
    return request.app.state.sessions.get(request.cookies.get(SESSION_COOKIE))


def require_session(request: Request) -> Session:
    s = session_of(request)
    if s is None:
        raise ApiError(401, "not_signed_in", "Please sign in.")
    request.state.user_id = s.user_id
    return s


def current_user(request: Request, con) -> User:
    """The signed-in member of staff, re-checked against the warehouse on every request (a deactivated account
    loses access at once)."""
    s = require_session(request)
    user = lookup_staff(con, s.user_id)
    if user is None or user.status != "active":
        request.app.state.sessions.delete(s.sid)
        raise ApiError(401, "not_signed_in", "Your account is no longer active.")
    return user


def require_role(user: User, roles) -> None:
    if user.role not in roles:
        raise ApiError(403, "forbidden", "Your role does not have access to this.")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


AS_OF = config.AS_OF_DATE
