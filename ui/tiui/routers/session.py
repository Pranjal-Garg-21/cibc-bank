"""Sign-in, sign-out and who you are (§9.1).

Staff sign in with their staff ID and password (accounts made by an administrator with ui/accounts.py) on one form
for every role. The browser never chooses or sends a role: the server reads it from curated.agents.role after the
password is checked, and the app opens that role's screens. Nothing about any member of staff is available before
sign-in, and a failed sign-in never says whether the staff ID exists.
"""
from __future__ import annotations

import importlib.util
import sys

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import rbac
from ..deps import AS_OF, SIGN_IN_ROLES, check_credentials, current_user, lookup_staff, require_reauthentication, require_session, warehouse
from ..errors import ApiError
from ..security import SESSION_COOKIE

router = APIRouter(prefix="/api/session", tags=["session"])

STAFF_ID = r"^[A-Za-z0-9_\-]{1,40}$"
FAILED = "The staff ID, password or verification code is incorrect."


def features() -> dict[str, bool]:
    """Which backend parts are installed in this build. Missing ones show a 'not connected' panel."""
    def has(mod):
        if mod in sys.modules:
            return sys.modules[mod] is not None
        try:
            return importlib.util.find_spec(mod) is not None
        except (ModuleNotFoundError, ValueError):
            return False
    return {"queue": has("trustissues.nba.agent_view") and has("trustissues.nba.review"),
            "ask": has("trustissues.ask.service"),
            "identity": True, "status": True}


@router.get("/options")
def options(request: Request):
    """What the sign-in page needs: nothing about staff, only whether sign-in is set up."""
    s = request.app.state.settings
    return {"sign_in_configured": request.app.state.accounts.configured(), "demo": s.demo,
            "as_of_date": AS_OF.isoformat(), "idle_minutes": s.idle_minutes if s.session_timeouts else None, "mfa_required": s.require_mfa,
            "shared_access": s.demo_password is not None}


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a role (or anything else) sent by the browser is refused, not used
    staff_id: str = Field(pattern=STAFF_ID)
    password: str = Field(min_length=1, max_length=128)
    verification_code: str = Field(default="", max_length=32)


def _cookie(response: Response, sess, settings) -> None:
    response.set_cookie(SESSION_COOKIE, sess.sid, httponly=True, samesite="strict", secure=settings.https,
                        path="/", max_age=settings.max_hours * 3600 if settings.session_timeouts else None)


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    app = request.app.state
    ip = request.client.host if request.client else "?"
    staff_id = body.staff_id.strip().upper()
    wait = app.limiter.hit("login", ip) or app.accounts.locked_for(staff_id)
    if wait:
        app.access_log.record(action="sign_in", outcome="locked", object_id=staff_id, request_id=request.state.request_id)
        raise ApiError(429, "rate_limited", f"Too many attempts. Try again in {max(1, wait // 60)} min.",
                       {"Retry-After": str(wait)})
    version = app.accounts.version(staff_id)
    ok = app.accounts.verify(staff_id, body.password, record=False)
    user = None
    if ok:
        with warehouse(app.settings) as con:
            user = lookup_staff(con, staff_id)
    # The role is the warehouse's (curated.agents.role), never the browser's. A member of staff who is inactive or
    # whose role has no screens here fails exactly like a wrong password: nothing is revealed about the account.
    ok = ok and user is not None and user.status == "active" and user.role in SIGN_IN_ROLES
    mfa = app.accounts.has_mfa(staff_id)
    if ok and (app.settings.require_mfa or mfa):
        ok = app.accounts.verify_mfa(staff_id, body.verification_code)
    ok = ok and app.accounts.version(staff_id) == version
    app.accounts.record_attempt(staff_id, bool(ok))
    if not ok:
        app.access_log.record(action="sign_in", outcome="refused", object_id=staff_id, request_id=request.state.request_id)
        raise ApiError(401, "sign_in_failed", FAILED)
    app.sessions.delete(request.cookies.get(SESSION_COOKIE))  # a new session id on every sign-in
    sess = app.sessions.create(user.agent_id, user.role, credential_version=version, mfa=mfa,
                               device=request.headers.get("user-agent", "Browser"))
    _cookie(response, sess, app.settings)
    app.access_log.record(action="sign_in", user_id=user.agent_id, role=user.role, request_id=request.state.request_id)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    s = require_session(request)
    request.app.state.sessions.delete(s.sid)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict",
                           secure=request.app.state.settings.https)
    response.headers["Clear-Site-Data"] = '"cache", "storage"'
    request.app.state.access_log.record(action="sign_out", user_id=s.user_id, role=s.role,
                                        request_id=request.state.request_id)
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    """The signed-in person's own record. Never anyone else's."""
    s = request.app.state.settings
    with warehouse(s) as con:
        user = current_user(request, con)
    sess = require_session(request)
    return {
        "agent_id": user.agent_id, "name": user.display_name, "role": user.role, "team_id": user.team_id,
        "certified": user.certified, "ask_role": user.ask_role, "csrf_token": sess.csrf,
        "site": user.site, "team_lead_id": user.team_lead_id, "languages": user.languages,
        "employment_type": user.employment_type, "signed_in_at": sess.signed_in_at,
        "max_hours": s.max_hours if s.session_timeouts else None, "as_of_date": AS_OF.isoformat(),
        "idle_minutes": s.idle_minutes if s.session_timeouts else None, "demo": s.demo,
        "features": features(),
        "mfa": sess.mfa,
        "shared_access": s.demo_password is not None,
        "transport_secure": request.url.scheme == "https",
        "can": rbac.can_map(user, s),
        "approval_kinds": list(rbac.approval_kinds(user.role)),
    }


@router.get("/state")
def state(request: Request):
    """Monitoring must never keep an idle session alive."""
    with warehouse(request.app.state.settings) as con:
        current_user(request, con, touch=False)
    sess = require_session(request, touch=False)
    return request.app.state.sessions.remaining(sess)


@router.post("/keepalive")
def keepalive(request: Request):
    with warehouse(request.app.state.settings) as con:
        current_user(request, con)
    return request.app.state.sessions.remaining(require_session(request))


class Reauthenticate(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    verification_code: str = Field(default="", max_length=32)


@router.post("/reauthenticate")
def reauthenticate(body: Reauthenticate, request: Request, response: Response):
    app = request.app.state
    with warehouse(app.settings) as con:
        user = current_user(request, con)
    old = require_session(request)
    check_credentials(request, user, old, body.password, body.verification_code, action="reauthenticate")
    sess = app.sessions.reauthenticate(old.sid)
    if sess is None:
        raise ApiError(401, "not_signed_in", "Your session has ended. Please sign in again.")
    _cookie(response, sess, app.settings)
    return {"ok": True, "csrf_token": sess.csrf}


@router.get("/security")
def security(request: Request):
    app = request.app.state
    with warehouse(app.settings) as con:
        user = current_user(request, con, touch=False)
    sess = require_session(request, touch=False)
    return {"sessions": app.sessions.for_user(user.agent_id, sess.sid),
            "events": app.access_log.recent_security(user.agent_id), "max_sessions": 3}


@router.post("/logout-others")
def logout_others(request: Request):
    app = request.app.state
    with warehouse(app.settings) as con:
        user = current_user(request, con)
    require_reauthentication(request)
    sess = require_session(request)
    count = app.sessions.delete_others(user.agent_id, sess.sid)
    app.access_log.record(action="sign_out_others", user_id=user.agent_id, role=user.role,
                          request_id=request.state.request_id)
    return {"ok": True, "ended": count}
