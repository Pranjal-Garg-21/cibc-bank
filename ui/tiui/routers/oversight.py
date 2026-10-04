"""Oversight: how decisions are being made (team leads for their team, QA for the bank) and the access log (QA only).

Read-only. Decision review reads gold.nba_overrides; the audit log reads the UI's own access log. Neither opens a
customer's case. Opening the audit log is itself logged, so oversight of the overseers is on record.
"""
from __future__ import annotations

import re
from datetime import timedelta

from fastapi import APIRouter, Query, Request

from .. import rbac
from ..deps import AS_OF, current_user, warehouse
from ..errors import ApiError
from ..nba_state import count_by, decision_rows, reason_labels, staff_names, team_agent_ids
from ..serialize import jsonable

router = APIRouter(prefix="/api/oversight", tags=["oversight"])

TEXT = r"^[A-Za-z0-9_:.\- ]{0,40}$"
MIN_FOR_OUTLIER = 5


def _limit(request: Request, user_id: str) -> None:
    wait = request.app.state.limiter.hit("read", user_id)
    if wait:
        raise ApiError(429, "rate_limited", f"Too many requests. Try again in {wait} s.", {"Retry-After": str(wait)})


@router.get("/decisions")
def decisions(request: Request, days: int = Query(14, ge=1, le=92)):
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        rbac.require(user, "decision_review")
        _limit(request, user.agent_id)
        team = user.role == "team_lead"
        ids = team_agent_ids(con, user.agent_id) if team else None  # a team lead sees their own team; QA the bank
        since = AS_OF - timedelta(days=days - 1)
        agent_rows = decision_rows(con, since, AS_OF, ids)
        approval_rows = decision_rows(con, since, AS_OF, [user.agent_id] if team else None, ("approved", "rejected"))
        names = staff_names(con, {r["agent_id"] for r in agent_rows + approval_rows})
    labels, safeguards = reason_labels()
    over = [r for r in agent_rows if r["decision"] == "overridden"]
    rate = len(over) / len(agent_rows) if agent_rows else None

    by_agent = []
    for aid in sorted({r["agent_id"] for r in agent_rows}):
        mine = [r for r in agent_rows if r["agent_id"] == aid]
        mo = [r for r in mine if r["decision"] == "overridden"]
        r = len(mo) / len(mine)
        top = max(count_by(mo, "reason_code").items(), key=lambda kv: kv[1], default=(None, 0))[0]
        by_agent.append({"agent_id": aid, "name": names.get(aid), "decisions": len(mine), "overrides": len(mo),
                         "rate": round(r, 4), "top_reason": labels.get(top, top),
                         "outlier": bool(rate is not None and len(mine) >= MIN_FOR_OUTLIER and r >= max(0.25, 1.5 * rate))})
    by_day = []
    for k in range((AS_OF - since).days + 1):
        d = since + timedelta(days=k)
        day = [r for r in agent_rows if r["day"] == d]
        by_day.append({"day": d, "decisions": len(day), "overrides": sum(r["decision"] == "overridden" for r in day)})
    reasons = count_by(over, "reason_code")
    rejected = [r for r in approval_rows if r["decision"] == "rejected"]
    return jsonable({
        "from": since, "to": AS_OF, "days": days, "scope": "team" if team else "bank",
        "totals": {"decisions": len(agent_rows), "accepted": len(agent_rows) - len(over), "overridden": len(over),
                   "override_rate": None if rate is None else round(rate, 4),
                   "approved": sum(r["decision"] == "approved" for r in approval_rows), "rejected": len(rejected)},
        "by_reason": sorted(({"code": k, "label": labels.get(k, k), "n": n, "safeguard": k in safeguards}
                             for k, n in reasons.items()), key=lambda r: -r["n"]),
        "rejections": sorted(({"code": k, "label": labels.get(k, k), "n": n}
                              for k, n in count_by(rejected, "reason_code").items()), key=lambda r: -r["n"]),
        "by_agent": sorted(by_agent, key=lambda r: -r["rate"]),
        "by_day": by_day,
    })


@router.get("/audit")
def audit(request: Request, hours: int = Query(24, ge=1, le=168), user: str = Query("", max_length=40),
          action: str = Query("", max_length=40), outcome: str = Query("", max_length=20),
          q: str = Query("", max_length=40), limit: int = Query(200, ge=1, le=500)):
    for v in (user, action, outcome, q):
        if not re.match(TEXT, v):
            raise ApiError(422, "invalid_input", "Filters use letters, numbers and simple punctuation only.")
    app = request.app.state
    with warehouse(app.settings) as con:
        me = current_user(request, con)
        rbac.require(me, "audit_log")
    _limit(request, me.agent_id)
    events, stats = app.access_log.search(hours=hours, user=user.strip(), action=action.strip(), outcome=outcome.strip(),
                                          text=q.strip(), limit=limit)
    app.access_log.record(action="view_audit_log", user_id=me.agent_id, role=me.role, object_id=f"{hours}h",
                          request_id=request.state.request_id)
    return {"hours": hours, "events": events, **stats}
