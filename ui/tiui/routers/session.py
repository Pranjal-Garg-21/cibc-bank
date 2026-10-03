"""Sign-in, sign-out and who you are (§9.1).

Staff sign in with their staff ID and password (accounts made by an administrator with ui/accounts.py). Nothing
about any member of staff is available before sign-in, and a failed sign-in never says whether the staff ID exists.
"""
from __future__ import annotations

import importlib.util
import sys

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from ..deps import AS_OF, PORTALS, current_user, lookup_staff, require_session, warehouse
from ..errors import ApiError
from ..security import SESSION_COOKIE

router = APIRouter(prefix="/api/session", tags=["session"])

STAFF_ID = r"^[A-Za-z0-9_\-]{1,40}$"
FAILED = "The staff ID or password is incorrect."


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
            "as_of_date": AS_OF.isoformat(), "idle_minutes": s.idle_minutes}


class Login(BaseModel):
    role: Literal["team_lead", "agent"]  # the tab chosen on the sign-in page
    staff_id: str = Field(pattern=STAFF_ID)
    password: str = Field(min_length=1, max_length=128)


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
    ok = app.accounts.verify(staff_id, body.password)
    user = None
    if ok:
        with warehouse(app.settings) as con:
            user = lookup_staff(con, staff_id)
    # Wrong role for the chosen tab fails exactly like a wrong password: nothing is revealed about the account.
    if not ok or user is None or user.status != "active" or user.role not in PORTALS[body.role]:
        app.access_log.record(action="sign_in", outcome="refused", object_id=staff_id, request_id=request.state.request_id)
        raise ApiError(401, "sign_in_failed", FAILED)
    app.sessions.delete(request.cookies.get(SESSION_COOKIE))  # a new session id on every sign-in
    sess = app.sessions.create(user.agent_id, user.role)
    response.set_cookie(SESSION_COOKIE, sess.sid, httponly=True, samesite="strict", secure=app.settings.https,
                        path="/", max_age=app.settings.max_hours * 3600)
    app.access_log.record(action="sign_in", user_id=user.agent_id, role=user.role, request_id=request.state.request_id)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    s = require_session(request)
    request.app.state.sessions.delete(s.sid)
    response.delete_cookie(SESSION_COOKIE, path="/")
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
        "max_hours": s.max_hours, "as_of_date": AS_OF.isoformat(), "idle_minutes": s.idle_minutes, "demo": s.demo,
        "features": features(),
        "can": {
            "ask": True,
            "my_queue": not user.is_supervisor,
            "approvals": user.role in ("team_lead", "hardship_specialist"),
            "manage_agents": user.role == "team_lead",
            "identity_review": user.role in s.identity_reviewer_roles,
            "status": True,
        },
    }
