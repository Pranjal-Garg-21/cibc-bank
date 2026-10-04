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

# Roles and capabilities live in rbac.py (one table, deny by default).
from .rbac import AGENT_ROLES, ASK_ROLE, SIGN_IN_ROLES, SUPERVISORS  # noqa: E402,F401


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
    site: str | None = None
    team_lead_id: str | None = None
    languages: str | None = None
    employment_type: str | None = None

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
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'curated' AND table_name = 'agents'")
        .fetchall()}
    opt = lambda c: c if c in cols else f"NULL AS {c}"  # noqa: E731  (optional profile fields)
    row = con.execute(f"""SELECT agent_id, role, team_id, coalesce(vulnerable_customer_certified, false), status,
                                 agent_display_name, {opt('site')}, {opt('team_lead_id')}, {opt('languages')},
                                 {opt('employment_type')}
                          FROM curated.agents WHERE agent_id = ?""", [agent_id]).fetchone()
    return User(*row) if row else None


BACKGROUND_HEADER = "x-ti-background"  # set by the browser on automatic refreshes (ui/web/src/api/client.ts)


def session_of(request: Request, touch: bool = True) -> Session | None:
    # An automatic refresh (live lists polling every 20 s) must never keep an idle session alive: only the person's
    # own activity does. Reads marked as background do not count as activity.
    if request.method == "GET" and request.headers.get(BACKGROUND_HEADER) == "1":
        touch = False
    return request.app.state.sessions.get(request.cookies.get(SESSION_COOKIE), touch=touch)


def require_session(request: Request, touch: bool = True) -> Session:
    s = session_of(request, touch=touch)
    if s is None:
        raise ApiError(401, "not_signed_in", "Please sign in.")
    request.state.user_id = s.user_id
    return s


def current_user(request: Request, con, touch: bool = True) -> User:
    """The signed-in member of staff, re-checked against the warehouse on every request (a deactivated account
    loses access at once)."""
    s = require_session(request, touch=touch)
    user = lookup_staff(con, s.user_id)
    if (user is None or user.status != "active" or user.role not in SIGN_IN_ROLES or user.role != s.role
            or s.credential_version is None
            or request.app.state.accounts.version(s.user_id) != s.credential_version):
        request.app.state.sessions.delete(s.sid)
        raise ApiError(401, "not_signed_in", "Your account is no longer active.")
    return user


def require_reauthentication(request: Request) -> None:
    """Account changes need a separate confirmation, valid for five minutes after a fresh challenge."""
    s = require_session(request)
    if s.reauthenticated is None or request.app.state.sessions.clock() - s.reauthenticated >= 300:
        raise ApiError(403, "reauthentication_required", "Confirm your identity before making this security change.")


def check_credentials(request: Request, user: User, sess: Session, password: str, code: str, action: str) -> None:
    """Re-check the signed-in person's password (and second factor, where enrolled or required) for one action.
    Rate-limited and counted towards the account lock like a sign-in; logged; 403 if wrong. Used to renew a session
    (/api/session/reauthenticate) and, every time, to confirm a digital batch approval (routers/nba.py)."""
    app = request.app.state
    wait = app.limiter.hit("reauth", user.agent_id) or app.accounts.locked_for(user.agent_id)
    if wait:
        raise ApiError(429, "rate_limited", "Too many attempts. Try again later.", {"Retry-After": str(wait)})
    ok = app.accounts.verify(user.agent_id, password, record=False)
    if ok and (app.settings.require_mfa or app.accounts.has_mfa(user.agent_id)):
        ok = app.accounts.verify_mfa(user.agent_id, code)
    ok = ok and app.accounts.version(user.agent_id) == sess.credential_version
    app.accounts.record_attempt(user.agent_id, bool(ok))
    app.access_log.record(action=action, outcome="ok" if ok else "refused", user_id=user.agent_id,
                          role=user.role, request_id=request.state.request_id)
    if not ok:
        if app.accounts.locked_for(user.agent_id):
            app.sessions.delete_user(user.agent_id)
        raise ApiError(403, "verification_failed", "The password or verification code is incorrect.")


def require_role(user: User, roles) -> None:
    if user.role not in roles:
        raise ApiError(403, "forbidden", "Your role does not have access to this.")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


AS_OF = config.AS_OF_DATE
