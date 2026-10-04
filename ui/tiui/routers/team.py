"""Agent accounts, managed by their team lead.

A team lead sees only the agents who report to them (curated.agents.team_lead_id) and sets each one's sign-in
password. That password is fixed: agents have no way to change it; only their team lead can reset it or remove the
account. Passwords are never returned or logged, only when and by whom they were set.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..accounts import check_password_rules
from ..deps import AGENT_ROLES, current_user, require_reauthentication, require_role, warehouse
from ..errors import ApiError

router = APIRouter(prefix="/api/team", tags=["team"])

STAFF_ID = r"^[A-Za-z0-9_\-]{1,40}$"


def _my_agents(con, lead_id: str) -> list[tuple]:
    roles = ", ".join(f"'{r}'" for r in AGENT_ROLES)
    return con.execute(f"""SELECT agent_id, agent_display_name, role, team_id FROM curated.agents
                           WHERE team_lead_id = ? AND status = 'active' AND role IN ({roles})
                           ORDER BY agent_display_name, agent_id""", [lead_id]).fetchall()


def _lead(request: Request, con):
    user = current_user(request, con)
    require_role(user, ("team_lead",))
    return user


def _my_agent(request: Request, agent_id: str):
    """The lead, after checking agent_id is an active agent reporting to them. Anything else is 404."""
    if not re.match(STAFF_ID, agent_id):
        raise ApiError(404, "not_found", "No such agent in your team.")
    with warehouse(request.app.state.settings) as con:
        lead = _lead(request, con)
        if agent_id not in {r[0] for r in _my_agents(con, lead.agent_id)}:
            raise ApiError(404, "not_found", "No such agent in your team.")
    return lead


def _limit(request: Request, user_id: str) -> None:
    wait = request.app.state.limiter.hit("write", user_id)
    if wait:
        raise ApiError(429, "rate_limited", f"Too many changes in a short time. Try again in {wait} s.",
                       {"Retry-After": str(wait)})


@router.get("/agents")
def agents(request: Request):
    with warehouse(request.app.state.settings) as con:
        lead = _lead(request, con)
        rows = _my_agents(con, lead.agent_id)
    info = request.app.state.accounts.all_info()
    shared = request.app.state.settings.demo_password is not None
    return {"team_lead": lead.agent_id, "agents": [
        {"agent_id": r[0], "name": r[1], "role": r[2], "team_id": r[3], "has_account": shared or r[0] in info,
         "set_at": info.get(r[0], {}).get("set_at"), "set_by": info.get(r[0], {}).get("set_by"),
         "mfa_enrolled": request.app.state.accounts.has_mfa(r[0])}
        for r in rows]}


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.post("/agents/{agent_id}/password")
def set_password(agent_id: str, body: PasswordIn, request: Request):
    app = request.app.state
    lead = _my_agent(request, agent_id)
    if app.settings.demo_password is not None:
        raise ApiError(409, "shared_demo_access", "Individual account changes are disabled during shared demo access.")
    require_reauthentication(request)
    _limit(request, lead.agent_id)
    problem = check_password_rules(body.password)
    if problem:
        raise ApiError(422, "weak_password", f"Password not accepted: {problem}.")
    app.accounts.set_password(agent_id, body.password, set_by=lead.agent_id)
    app.sessions.delete_user(agent_id)
    app.access_log.record(action="set_agent_password", user_id=lead.agent_id, role=lead.role, object_id=agent_id,
                          request_id=request.state.request_id)
    return {"ok": True, **app.accounts.info(agent_id)}


@router.post("/agents/{agent_id}/revoke")
def revoke(agent_id: str, request: Request):
    app = request.app.state
    lead = _my_agent(request, agent_id)
    if app.settings.demo_password is not None:
        raise ApiError(409, "shared_demo_access", "Individual account changes are disabled during shared demo access.")
    require_reauthentication(request)
    _limit(request, lead.agent_id)
    removed = app.accounts.remove(agent_id)
    if removed:
        app.sessions.delete_user(agent_id)  # sign the agent out everywhere at once
    app.access_log.record(action="revoke_agent_account", user_id=lead.agent_id, role=lead.role, object_id=agent_id,
                          outcome="ok" if removed else "no_account", request_id=request.state.request_id)
    return {"ok": True, "removed": removed}
