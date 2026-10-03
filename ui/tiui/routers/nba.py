"""Agent queue, decision card and review (P3's nba.agent_view and nba.review), imported lazily.

Until P3's branch is merged these endpoints answer 501 not_connected. All role rules are the backend's own:
agent_view.card raises AccessDenied, review.record raises ReviewError; the UI adds nothing it could get wrong.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..deps import AS_OF, current_user, require_role, warehouse
from ..errors import ApiError, not_connected
from ..security import MASK, sensitive_columns
from ..serialize import jsonable

router = APIRouter(prefix="/api", tags=["queue"])

REC_ID = r"^[A-Za-z0-9_\-:.]{1,80}$"
APPROVER_ROLES = ("team_lead", "hardship_specialist")


def _nba():
    try:
        from trustissues.features import sources
        from trustissues.nba import agent_view, review
    except ImportError as e:
        raise not_connected("The agent queue") from e
    return sources, agent_view, review


def _scrub(value):
    """agent_view returns no PII by design; as a second line, blank any column sensitivity.yaml marks sensitive."""
    v = jsonable(value)
    sens = sensitive_columns()

    def walk(x):
        if isinstance(x, dict):
            if set(x) == {"columns", "rows"}:
                hide = [i for i, c in enumerate(x["columns"]) if c.lower() in sens]
                return {"columns": x["columns"], "rows": [[MASK if i in hide and r[i] is not None else r[i]
                                                           for i in range(len(r))] for r in x["rows"]]}
            return {k: (MASK if k.lower() in sens and val is not None else walk(val)) for k, val in x.items()}
        if isinstance(x, list):
            return [walk(i) for i in x]
        return x
    return walk(v)


def _limit(request: Request, user_id: str) -> None:
    wait = request.app.state.limiter.hit("write", user_id)
    if wait:
        raise ApiError(429, "rate_limited", f"Too many changes in a short time. Try again in {wait} s.",
                       {"Retry-After": str(wait)})


@router.get("/queue/mine")
def my_queue(request: Request):
    sources, agent_view, _ = _nba()
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        sources.register(con)
        q = agent_view.agent_queue(con, user.agent_id, AS_OF)
    return _scrub({"as_of_date": AS_OF, "queue": q})


@router.get("/queue/digest")
def digest(request: Request):
    sources, agent_view, _ = _nba()
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        require_role(user, agent_view.SUPERVISORS)
        sources.register(con)
        d = agent_view.lead_digest(con, AS_OF)
    return _scrub({"as_of_date": AS_OF, **d})


@router.get("/cases/{rec_id}")
def case(rec_id: str, request: Request):
    sources, agent_view, review = _nba()
    if not re.match(REC_ID, rec_id):
        raise ApiError(404, "not_found", "No such recommendation.")
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        sources.register(con)
        try:
            rec = agent_view.card(con, rec_id, user.as_staff())
        except KeyError:
            raise ApiError(404, "not_found", "No such recommendation.")
        except agent_view.AccessDenied:
            raise ApiError(403, "forbidden", "This recommendation is not assigned to you.")
        ctx = agent_view.case_context(con, rec["case_id"], AS_OF)
    request.app.state.access_log.record(action="open_case", user_id=user.agent_id, role=user.role, object_id=rec_id,
                                        request_id=request.state.request_id)
    reason = rec.get("approval_reason")
    return _scrub({
        "recommendation": rec, "context": ctx,
        "allowed": {
            "agent_decision": rec.get("agent_id") == user.agent_id,
            "approval": bool(rec.get("requires_approval")) and user.role in review.APPROVERS.get(reason, ()),
        },
    })


@router.get("/review/reasons")
def reasons(request: Request):
    _, _, review = _nba()
    with warehouse(request.app.state.settings) as con:
        current_user(request, con)
    return {"reasons": [{"code": k, "label": v} for k, v in review.REASON_CODES.items()],
            "safeguard_reasons": list(review.SAFEGUARD_REASONS)}


class DecisionIn(BaseModel):
    decision: Literal["accepted", "overridden", "approved", "rejected"]
    reason_code: str | None = Field(None, max_length=60, pattern=r"^[a-z_]+$")


@router.post("/review/{rec_id}")
def decide(rec_id: str, body: DecisionIn, request: Request):
    _, _, review = _nba()
    if not re.match(REC_ID, rec_id):
        raise ApiError(404, "not_found", "No such recommendation.")
    if body.reason_code is not None and body.reason_code not in review.REASON_CODES:
        raise ApiError(422, "invalid_input", "Unknown reason code.")
    with warehouse(request.app.state.settings, write=True) as con:
        user = current_user(request, con)
        _limit(request, user.agent_id)
        try:
            row = review.record(con, rec_id, user.agent_id, body.decision, body.reason_code)
        except review.ReviewError as e:
            request.app.state.access_log.record(action=f"decision:{body.decision}", outcome="refused",
                                                user_id=user.agent_id, role=user.role, object_id=rec_id,
                                                request_id=request.state.request_id)
            raise ApiError(409, "not_allowed", str(e))
    request.app.state.access_log.record(action=f"decision:{body.decision}", user_id=user.agent_id, role=user.role,
                                        object_id=rec_id, request_id=request.state.request_id)
    return jsonable({"ok": True, "row": row})


class BatchIn(BaseModel):
    rejected: dict[str, str] = Field(default_factory=dict, max_length=5000)


@router.post("/review-batch")
def approve_batch(body: BatchIn, request: Request):
    _, _, review = _nba()
    for rec_id, code in body.rejected.items():
        if not re.match(REC_ID, rec_id) or code not in review.REASON_CODES:
            raise ApiError(422, "invalid_input", "Each pulled-out case needs a known reason code.")
    with warehouse(request.app.state.settings, write=True) as con:
        user = current_user(request, con)
        require_role(user, review.APPROVERS["digital_batch"])
        _limit(request, user.agent_id)
        try:
            res = review.approve_batch(con, user.agent_id, AS_OF, body.rejected or None)
        except review.ReviewError as e:
            raise ApiError(409, "not_allowed", str(e))
    request.app.state.access_log.record(action="approve_batch", user_id=user.agent_id, role=user.role,
                                        object_id=f"{len(body.rejected)} pulled out",
                                        request_id=request.state.request_id)
    return jsonable({"ok": True, "result": res})


@router.get("/review/summary")
def summary(request: Request, days: int = Query(7, ge=1, le=92)):
    _, agent_view, review = _nba()
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        require_role(user, agent_view.SUPERVISORS)
        s = review.summary(con, AS_OF - timedelta(days=days - 1), AS_OF)
    return _scrub({"from": AS_OF - timedelta(days=days - 1), "to": AS_OF, **s})

