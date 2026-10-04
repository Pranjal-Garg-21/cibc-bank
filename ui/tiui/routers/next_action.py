"""Next best action by customer: find a customer and see the recommended treatment, channel and timing.

Reads gold.nba_decisions directly (written by P3's nba/queue.py), so it works whether or not trustissues.nba is
importable in this build. Who sees which customers comes from rbac.customer_scope: an agent only their own, a
hardship specialist only hardship, vulnerability and support-call cases, a team lead all, QA none. A customer with
nothing visible answers 404, so the screen never reveals whether someone else's customer exists.

The customer list is paged (every customer the role may see, `page_size` at a time, with the total), and each
recommendation carries its live state from gold.nba_overrides (nba_state.state_rows, the same rule as nba.review), so
an approved case reads "approved", not the static "needs approval" of gold.nba_decisions.
"""
from __future__ import annotations

import json
import re

import duckdb
from fastapi import APIRouter, Query, Request

from .. import rbac
from ..deps import AS_OF, current_user, warehouse
from ..errors import ApiError, not_connected
from ..nba_state import state_rows
from .nba import _scrub

router = APIRouter(prefix="/api/next-action", tags=["next best action"])

CUSTOMER_ID = r"^[A-Za-z0-9_\-:.]{1,40}$"
SEARCH = r"^[A-Za-z0-9_\-:.]{0,40}$"
# Same working order as the queue (nba.agent_view.ACTION_ORDER): the first row is the one contact made today.
ACTION_RANK = "CASE action WHEN 'support_call' THEN 0 WHEN 'agent_call' THEN 1 WHEN 'wait' THEN 3 ELSE 2 END"
ORDER_SQL = f"{ACTION_RANK}, expected_value DESC NULLS LAST, risk_score DESC NULLS LAST"


def _query(con, sql: str, params: list):
    try:
        return con.execute(sql, params)
    except duckdb.CatalogException as e:
        raise not_connected("Next best action") from e


@router.get("/customers")
def search(request: Request, q: str = Query("", max_length=40), page: int = Query(1, ge=1, le=100_000),
           page_size: int = Query(50, ge=10, le=200)):
    q = q.strip()
    if not re.match(SEARCH, q):
        raise ApiError(422, "invalid_input", "Customer IDs use letters, numbers and dashes only.")
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        rbac.require(user, "next_action")
        where, params = rbac.customer_scope(user)
        base = f"""FROM gold.nba_decisions
                   WHERE as_of_date = ? AND golden_customer_id IS NOT NULL AND golden_customer_id ILIKE ?{where}"""
        total = _query(con, f"SELECT count(DISTINCT golden_customer_id) {base}", [AS_OF, f"%{q}%", *params]).fetchone()[0]
        rows = _query(con, f"""
            SELECT golden_customer_id AS customer_id, count(*) AS cases,
                   arg_min(action, {ACTION_RANK}) AS action,
                   arg_min(channel, {ACTION_RANK}) AS channel
            {base}
            GROUP BY 1 ORDER BY golden_customer_id NOT ILIKE ?, golden_customer_id LIMIT ? OFFSET ?""",
                      [AS_OF, f"%{q}%", *params, f"{q}%", page_size, (page - 1) * page_size]).fetchall()
    return {"as_of_date": AS_OF, "total": total, "page": page, "page_size": page_size,
            "customers": [{"customer_id": c, "cases": n, "action": a, "channel": ch} for c, n, a, ch in rows]}


@router.get("/customers/{customer_id}")
def customer(customer_id: str, request: Request):
    if not re.match(CUSTOMER_ID, customer_id):
        raise ApiError(404, "not_found", "No recommendation for this customer today.")
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        rbac.require(user, "next_action")
        where, params = rbac.customer_scope(user)
        cur = _query(con, f"""
            SELECT recommendation_id, case_id, agent_id, as_of_date, action, channel, risk_score, expected_value,
                   requires_approval, approval_reason, decision_card, model_version
            FROM gold.nba_decisions WHERE as_of_date = ? AND golden_customer_id = ?{where}
            ORDER BY {ORDER_SQL}""", [AS_OF, customer_id, *params])
        cols = [d[0] for d in cur.description]
        recs = [dict(zip(cols, r)) for r in cur.fetchall()]
        live = {r["recommendation_id"]: r for r in state_rows(con, AS_OF, f" AND golden_customer_id = ?{where}",
                                                              [customer_id, *params])} if recs else {}
    if not recs:
        raise ApiError(404, "not_found", "No recommendation for this customer today.")
    for r in recs:
        st = live.get(r["recommendation_id"], {})
        r["status"] = {"state": st.get("state"), "approval": st.get("approval_decision") or (
                           "pending" if r.get("requires_approval") else "not_needed"),
                       "agent": st.get("agent_decision"), "completed": bool(st.get("completed")),
                       "outcome": st.get("outcome")}
        try:
            r["card"] = json.loads(r.pop("decision_card") or "{}")
        except ValueError:
            r["card"] = {}
    request.app.state.access_log.record(action="open_next_action", user_id=user.agent_id, role=user.role,
                                        object_id=customer_id, request_id=request.state.request_id)
    return _scrub({"as_of_date": AS_OF, "customer_id": customer_id, "recommendations": recs})
