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

from .. import rbac
from ..deps import AS_OF, check_credentials, current_user, require_reauthentication, require_session, warehouse
from ..nba_state import staff_names
from ..errors import ApiError, not_connected
from ..security import MASK, sensitive_columns
from ..serialize import jsonable

router = APIRouter(prefix="/api", tags=["queue"])

REC_ID = r"^[A-Za-z0-9_\-:.]{1,80}$"
_EMPTY = {"columns": [], "rows": []}


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
        rbac.require(user, "my_queue")
        sources.register(con)
        q = agent_view.agent_queue(con, user.agent_id, AS_OF)
    return _scrub({"as_of_date": AS_OF, "queue": q})


@router.get("/queue/digest")
def digest(request: Request):
    sources, agent_view, review = _nba()
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        rbac.require(user, "approvals")
        sources.register(con)
        d = agent_view.lead_digest(con, AS_OF)
    kinds = rbac.approval_kinds(user.role)
    pending = d["pending"]
    if len(pending):  # a role sees only the kinds of approval it may give
        pending = pending[pending["approval_reason"].isin(kinds)]
    over = d.get("overridden")
    if over is not None and len(over):
        # the archive of overridden cases, in the same remit as the approvals (a specialist: support cases only);
        # each with who overrode it and the reason, in words (review.REASON_CODES)
        if user.role != "team_lead":
            over = over[over["approval_reason"].isin(kinds) | (over["action"] == "support_call")]
        with warehouse(request.app.state.settings) as con:
            names = staff_names(con, set(over["overridden_by"].dropna()))
        over = over.assign(overridden_by_name=over["overridden_by"].map(names),
                           reason=over["reason_code"].map(lambda c: review.REASON_CODES.get(c, c)))
    d = {**d, "pending": pending, "overridden": over if over is not None else _EMPTY}
    if not rbac.has(user.role, "approve_batch"):  # the batch and the bank-wide counts are the team lead's
        d = {**d, "states": {}, "batch_pending": 0, "batch_total": 0}
    return _scrub({"as_of_date": AS_OF, "approval_kinds": list(kinds), **d})


@router.get("/cases/{rec_id}")
def case(rec_id: str, request: Request):
    sources, agent_view, review = _nba()
    if not re.match(REC_ID, rec_id):
        raise ApiError(404, "not_found", "No such recommendation.")
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        rbac.require(user, "case_view")
        sources.register(con)
        try:
            rec = agent_view.card(con, rec_id, user.as_staff())
        except KeyError:
            raise ApiError(404, "not_found", "No such recommendation.")
        except agent_view.AccessDenied:  # same answer as a recommendation that does not exist: no existence oracle
            raise ApiError(404, "not_found", "No such recommendation.")
        if user.role == "hardship_specialist" and rec.get("agent_id") != user.agent_id and not (
                rec.get("approval_reason") in rbac.SUPPORT_APPROVALS or rec.get("action") == "support_call"):
            # outside the specialist's remit: same answer as a recommendation that does not exist
            raise ApiError(404, "not_found", "No such recommendation.")
        ctx = agent_view.case_context(con, rec["case_id"], AS_OF)
        # the person behind the case (identity resolution): all their cases and hardship offers across them, so the
        # agent sees that safeguards, the 7-day contact limit and hardship history are the person's, not the case's
        person = agent_view.customer_context(con, rec["golden_customer_id"], AS_OF) \
            if rec.get("golden_customer_id") else {"customer": None, "cases": _EMPTY, "offers": _EMPTY}
    request.app.state.access_log.record(action="open_case", user_id=user.agent_id, role=user.role, object_id=rec_id,
                                        request_id=request.state.request_id)
    reason = rec.get("approval_reason")
    st = rec.get("status") or {}
    # the decision history in words: who decided (name and staff ID) and the reason's label, not its code
    hist = rec.get("history")
    override = None
    if hist is not None and len(hist):
        hist = hist.reindex(columns=["ts", "decided_by", "decision", "reason_code"])
        with warehouse(request.app.state.settings) as con:
            names = staff_names(con, set(hist["decided_by"].dropna()))
        labels = {**review.REASON_CODES, **review.CALL_OUTCOMES}
        hist = hist.assign(name=hist["decided_by"].map(names),
                           reason=hist["reason_code"].map(lambda c: labels.get(c, c) if c else None))
        rec["history"] = hist[["ts", "decided_by", "name", "decision", "reason"]]
        if st.get("agent") == "overridden":  # who overrode it and why, shown at the top of the card
            last = hist[hist["decision"] == "overridden"].iloc[-1]
            override = {"by": last["decided_by"], "name": last["name"], "reason_code": last["reason_code"],
                        "reason": last["reason"], "at": last["ts"]}
    assigned = rec.get("agent_id") == user.agent_id and rbac.has(user.role, "decide")
    completed = st.get("completion") == "completed"  # a call that has been made: nothing more to decide
    return _scrub({
        "recommendation": rec, "context": ctx, "customer": person, "override": override,
        "allowed": {
            "agent_decision": assigned and not completed,
            # the assigned agent marks a cleared call as made, with its outcome
            "complete": assigned and rec.get("action") in rbac.CALL_ACTIONS and st.get("state") == "ready",
            # a second person approves (D5): never the member of staff the recommendation is assigned to
            "approval": bool(rec.get("requires_approval")) and user.role in review.APPROVERS.get(reason, ())
                        and rec.get("agent_id") != user.agent_id
                        and st.get("agent") != "overridden" and not completed,  # nothing left to approve
        },
    })


@router.get("/review/reasons")
def reasons(request: Request):
    _, _, review = _nba()
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        rbac.require(user, "case_view")
    return {"reasons": [{"code": k, "label": v} for k, v in review.REASON_CODES.items()],
            "safeguard_reasons": list(review.SAFEGUARD_REASONS),
            "call_outcomes": [{"code": k, "label": v} for k, v in review.CALL_OUTCOMES.items()]}


def _assignee(con, rec_id: str) -> str | None:
    row = con.execute("SELECT agent_id FROM gold.nba_decisions WHERE recommendation_id = ?", [rec_id]).fetchone()
    return row[0] if row else None


class DecisionIn(BaseModel):
    decision: Literal["accepted", "overridden", "approved", "rejected", "completed"]
    reason_code: str | None = Field(None, max_length=60, pattern=r"^[a-z_]+$")


@router.post("/review/{rec_id}")
def decide(rec_id: str, body: DecisionIn, request: Request):
    _, _, review = _nba()
    if not re.match(REC_ID, rec_id):
        raise ApiError(404, "not_found", "No such recommendation.")
    known = review.CALL_OUTCOMES if body.decision == "completed" else review.REASON_CODES
    if body.reason_code is not None and body.reason_code not in known:
        raise ApiError(422, "invalid_input", "Unknown reason code.")
    with warehouse(request.app.state.settings, write=True) as con:
        user = current_user(request, con)
        rbac.require(user, "approvals" if body.decision in ("approved", "rejected") else "decide")
        if body.decision in ("approved", "rejected"):
            require_reauthentication(request)
        _limit(request, user.agent_id)
        try:
            if body.decision in ("approved", "rejected") and _assignee(con, rec_id) == user.agent_id:
                raise review.ReviewError("a recommendation is approved by someone other than the person it is assigned to")
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
    # The batch signs off every automated reminder of the day at once, so it is confirmed with the password every
    # time (D38), in the request itself: a recent re-authentication for another action does not count.
    password: str = Field(min_length=1, max_length=128)
    verification_code: str = Field(default="", max_length=32)
    rejected: dict[str, str] = Field(default_factory=dict, max_length=5000)


@router.post("/review-batch")
def approve_batch(body: BatchIn, request: Request):
    _, _, review = _nba()
    for rec_id, code in body.rejected.items():
        if not re.match(REC_ID, rec_id) or code not in review.REASON_CODES:
            raise ApiError(422, "invalid_input", "Each pulled-out case needs a known reason code.")
    with warehouse(request.app.state.settings, write=True) as con:
        user = current_user(request, con)
        rbac.require(user, "approve_batch")
        check_credentials(request, user, require_session(request), body.password, body.verification_code,
                          action="confirm_batch")
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
        rbac.require(user, "decision_review")
        s = review.summary(con, AS_OF - timedelta(days=days - 1), AS_OF)
    return _scrub({"from": AS_OF - timedelta(days=days - 1), "to": AS_OF, **s})
