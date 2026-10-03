"""Sign-in, sign-out and who you are (§9.1). Demo sign-in is a staff picker, available only with UI_DEMO_LOGIN=1."""
from __future__ import annotations

import importlib.util
import sys

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from ..deps import AS_OF, current_user, lookup_staff, require_session, warehouse
from ..errors import ApiError
from ..security import SESSION_COOKIE

router = APIRouter(prefix="/api/session", tags=["session"])

AGENT_ID = r"^[A-Za-z0-9_\-]{1,40}$"


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
    s = request.app.state.settings
    return {"demo_login": s.demo_login, "as_of_date": AS_OF.isoformat(), "idle_minutes": s.idle_minutes}


@router.get("/staff")
def staff(request: Request, q: str = Query("", max_length=40)):
    """Demo login picker: active staff matching q (id, name, role or team), at most 50."""
    s = request.app.state.settings
    if not s.demo_login:
        raise ApiError(404, "not_found", "Not found.")
    like = f"%{q.strip().lower()}%"
    with warehouse(s) as con:
        rows = con.execute("""
            SELECT agent_id, agent_display_name, role, team_id FROM curated.agents
            WHERE status = 'active' AND (lower(agent_id) LIKE ? OR lower(agent_display_name) LIKE ?
                                         OR lower(role) LIKE ? OR lower(team_id) LIKE ?)
            ORDER BY CASE role WHEN 'team_lead' THEN 0 WHEN 'hardship_specialist' THEN 1 WHEN 'qa_analyst' THEN 2
                     ELSE 3 END, agent_id
            LIMIT 50""", [like] * 4).fetchall()
    return {"staff": [{"agent_id": r[0], "name": r[1], "role": r[2], "team_id": r[3]} for r in rows]}


class Login(BaseModel):
    agent_id: str = Field(pattern=AGENT_ID)


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    app = request.app.state
    if not app.settings.demo_login:
        raise ApiError(404, "not_found", "Not found.")
    ip = request.client.host if request.client else "?"
    wait = app.limiter.hit("login", ip)
    if wait:
        raise ApiError(429, "rate_limited", f"Too many sign-in attempts. Try again in {wait} s.",
                       {"Retry-After": str(wait)})
    with warehouse(app.settings) as con:
        user = lookup_staff(con, body.agent_id)
    if user is None or user.status != "active":
        app.access_log.record(action="sign_in", outcome="refused", object_id=body.agent_id,
                              request_id=request.state.request_id)
        raise ApiError(401, "sign_in_failed", "That member of staff cannot sign in.")
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
    s = request.app.state.settings
    with warehouse(s) as con:
        user = current_user(request, con)
    sess = require_session(request)
    f = features()
    return {
        "agent_id": user.agent_id, "name": user.display_name, "role": user.role, "team_id": user.team_id,
        "certified": user.certified, "ask_role": user.ask_role, "csrf_token": sess.csrf,
        "as_of_date": AS_OF.isoformat(), "idle_minutes": s.idle_minutes, "demo": s.demo_login,
        "features": f,
        "can": {
            "ask": True,
            "my_queue": not user.is_supervisor,
            "approvals": user.role in ("team_lead", "hardship_specialist"),
            "identity_review": user.role in s.identity_reviewer_roles,
            "status": True,
        },
    }
