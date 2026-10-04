"""The first screen of each role: key figures, where the day stands and what to do next.

Each role's page is assembled here, on the server, from rbac.customer_scope and the role's own remit, so the browser
never receives a figure its role may not see. Customer-level rows go only to roles that may open those customers;
QA gets aggregates. Read-only: every number comes from gold.nba_decisions / gold.nba_overrides.

The reply is a small description of a page (tiles and sections) that the browser draws without knowing the role:
    tile     {id, label, value, note?, tone?, to?}
    section  {id, title, description?, kind: 'bars' | 'table', ...}   (`to` makes a tile, bar or row a link)
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Request

from .. import rbac
from ..deps import AS_OF, current_user, warehouse
from ..errors import ApiError
from ..nba_state import (count_by, decision_rows, have_decisions, state_rows, staff_names, team_agent_ids)
from ..rbac import CALL_ACTIONS, SUPPORT_APPROVALS
from ..serialize import jsonable

router = APIRouter(prefix="/api", tags=["overview"])

ACTION_ORDER = {"support_call": 0, "agent_call": 1, "sms_reminder": 2, "email_reminder": 2, "push_reminder": 2,
                "letter": 2, "wait": 3}
ACTION_NAME = {"support_call": "Support call", "agent_call": "Call", "sms_reminder": "SMS reminder",
               "email_reminder": "Email reminder", "push_reminder": "Push reminder", "letter": "Letter", "wait": "Wait"}
CHANNEL_NAME = {"phone": "Phone call", "sms": "SMS", "email": "Email", "mail": "Letter by post", "push": "App push",
                "none": "No contact"}
APPROVAL_NAME = {"vulnerability": "Vulnerability", "hardship": "Hardship", "high_balance": "High balance",
                 "digital_batch": "Digital batch"}
SUPPORT_NAME = {"offer_programme": "Offer a hardship programme", "plan_ending": "Hardship plan ending soon",
                "follow_up": "Follow up on a hardship offer", "vulnerable_contact": "Vulnerable customer due for contact",
                "none": "Other"}
STATE_NAME = {"waiting_agent": "Agent to decide", "waiting_approval": "To approve", "ready": "Ready",
              "overridden": "Overridden", "blocked": "Rejected"}
SAFEGUARDS = ("hardship_disclosed", "vulnerability_concern", "dispute_raised")


def _tile(id, label, value, note=None, tone=None, to=None, kind=None):
    return {"id": id, "label": label, "value": value, "note": note, "tone": tone, "to": to, "kind": kind}


def _bars(id, title, counts: dict, names: dict, link=None, description=None, order=None):
    keys = sorted(counts, key=(lambda k: order.get(k, 9)) if order else (lambda k: -counts[k]))
    return {"id": id, "title": title, "description": description, "kind": "bars",
            "items": [{"label": names.get(k, k.replace("_", " ").capitalize()), "value": counts[k],
                       "to": link(k) if link else None} for k in keys]}


def _table(id, title, columns, rows, description=None, empty="Nothing to show."):
    return {"id": id, "title": title, "description": description, "kind": "table", "columns": columns, "rows": rows,
            "empty": empty}


def _pct(n, d):
    return round(n / d, 4) if d else None


def _risk_band(r):
    return f"{min(int((r or 0) * 5), 4) * 20}–{min(int((r or 0) * 5), 4) * 20 + 20}%"


def _agent(con, user):
    rows = state_rows(con, AS_OF, " AND agent_id = ?", [user.agent_id])
    by_state = count_by(rows, "state")
    waiting = sorted((r for r in rows if r["state"] == "waiting_agent"),
                     key=lambda r: (ACTION_ORDER.get(r["action"], 9), -(r["expected_value"] or 0)))
    since = AS_OF - timedelta(days=6)
    mine = decision_rows(con, since, AS_OF, [user.agent_id])
    overrides = sum(d["decision"] == "overridden" for d in mine)
    return {
        "headline": "Your day",
        "intro": "Calls and support calls come first, then reminders. Open a case to accept or override it.",
        "tiles": [
            _tile("recs", "Recommendations today", len(rows), f"Business date {AS_OF:%Y-%m-%d}", to="/queue"),
            _tile("waiting", "Waiting for your decision", by_state.get("waiting_agent", 0),
                  "Calls need your accept or override", "warn" if by_state.get("waiting_agent") else "ok",
                  "/queue?state=waiting_agent"),
            _tile("ready", "Ready to carry out", by_state.get("ready", 0), to="/queue?state=ready"),
            _tile("override", "Your override rate (7 days)", _pct(overrides, len(mine)) if mine else None,
                  f"{overrides} of {len(mine)} decisions" if mine else "No decisions yet", kind="percent"),
        ],
        "sections": [
            _table("next", "Up next", [
                {"key": "customer", "label": "Customer"}, {"key": "action", "label": "Treatment"},
                {"key": "channel", "label": "Channel"}, {"key": "risk", "label": "Risk", "kind": "percent"},
                {"key": "value", "label": "Value (CAD)", "kind": "money"}],
                [{"customer": r["golden_customer_id"], "action": ACTION_NAME.get(r["action"], r["action"]),
                  "channel": CHANNEL_NAME.get(r["channel"] or "none"), "risk": r["risk_score"],
                  "value": r["expected_value"], "_to": f"/cases/{r['recommendation_id']}"} for r in waiting[:8]],
                "The cases that need you first. Select one to open it.", "Nothing is waiting for you."),
            _bars("treat", "Today's treatments", count_by(rows, "action"), ACTION_NAME,
                  lambda k: f"/queue?action={k}", "Select a bar to filter your queue.", ACTION_ORDER),
            _bars("chan", "Channels", count_by(rows, "channel"), CHANNEL_NAME),
        ],
    }


def _team_lead(con, user):
    rows = state_rows(con, AS_OF)
    by_state = count_by(rows, "state")
    pending = [r for r in rows if r["approval_pending"]]  # needs a sign-off, whatever the agent has done
    pend_kind = count_by(pending, "approval_reason")
    team_ids = team_agent_ids(con, user.agent_id)
    names = staff_names(con, team_ids)
    since = AS_OF - timedelta(days=6)
    dec = decision_rows(con, since, AS_OF, team_ids)
    ov = sum(d["decision"] == "overridden" for d in dec)
    table = []
    for aid in team_ids:
        mine = [r for r in rows if r["agent_id"] == aid]
        st = count_by(mine, "state")
        md = [d for d in dec if d["agent_id"] == aid]
        mo = sum(d["decision"] == "overridden" for d in md)
        table.append({"agent": aid, "name": names.get(aid), "cases": len(mine), "waiting": st.get("waiting_agent", 0),
                      "ready": st.get("ready", 0), "overridden": st.get("overridden", 0),
                      "rate": _pct(mo, len(md)), "_to": f"/decision-review?agent={aid}"})
    non_batch = sum(v for k, v in pend_kind.items() if k != "digital_batch")
    return {
        "headline": "Your team today",
        "intro": "Approve what needs a second sign-off, keep an eye on workload, and review overrides.",
        "tiles": [
            _tile("approvals", "Case approvals waiting", non_batch, "Vulnerability, hardship, high balance",
                  "warn" if non_batch else "ok", "/approvals"),
            _tile("batch", "Digital batch waiting", pend_kind.get("digital_batch", 0), "Reminders with no agent",
                  "warn" if pend_kind.get("digital_batch") else "ok", "/approvals"),
            _tile("agents", "Agents still to decide", by_state.get("waiting_agent", 0), "Across the bank today"),
            _tile("override", "Team override rate (7 days)", _pct(ov, len(dec)) if dec else None,
                  f"{ov} of {len(dec)} decisions" if dec else "No decisions yet", to="/decision-review", kind="percent"),
        ],
        "sections": [
            _bars("states", "Where today's recommendations stand", by_state, STATE_NAME,
                  order={"waiting_agent": 0, "waiting_approval": 1, "ready": 2, "overridden": 3, "blocked": 4}),
            _bars("kinds", "Approvals waiting, by type", pend_kind, APPROVAL_NAME, lambda k: f"/approvals?reason={k}",
                  "Select a type to see just those cases.", {k: i for i, k in enumerate(rbac.ALL_APPROVALS)}),
            _table("team", "Your team", [
                {"key": "agent", "label": "Staff ID"}, {"key": "name", "label": "Name"},
                {"key": "cases", "label": "Cases", "kind": "int"}, {"key": "waiting", "label": "To decide", "kind": "int"},
                {"key": "ready", "label": "Ready", "kind": "int"}, {"key": "overridden", "label": "Overridden", "kind": "int"},
                {"key": "rate", "label": "Override rate (7 d)", "kind": "percent"}], table,
                "Select an agent to review their decisions.", "No agents report to you."),
        ],
    }


def _specialist(con, user):
    where, params = rbac.customer_scope(user)
    rows = state_rows(con, AS_OF, where, params)
    pending = sorted((r for r in rows if r["approval_pending"] and r["approval_reason"] in SUPPORT_APPROVALS),
                     key=lambda r: -(r["risk_score"] or 0))
    kinds = count_by(pending, "approval_reason")
    support = [r for r in rows if r["action"] == "support_call"]
    programmes: Counter = Counter()
    if rows:
        import json
        ids = [r["recommendation_id"] for r in rows]
        for (card,) in con.execute(f"SELECT decision_card FROM gold.nba_decisions WHERE recommendation_id IN "
                                   f"({', '.join('?' for _ in ids)})", ids).fetchall():
            try:
                for o in json.loads(card or "{}").get("hardship_options") or []:
                    programmes[str(o.get("name") or o.get("program_id") or "Programme")] += 1
            except ValueError:
                pass
    return {
        "headline": "Hardship and vulnerability desk",
        "intro": "Cases where a customer may be struggling or vulnerable. Offer support before asking for payment.",
        "tiles": [
            _tile("waiting", "Waiting for your approval", len(pending), "Hardship and vulnerability",
                  "warn" if pending else "ok", "/approvals"),
            _tile("vuln", "Vulnerability", kinds.get("vulnerability", 0), to="/approvals?reason=vulnerability"),
            _tile("hard", "Hardship", kinds.get("hardship", 0), to="/approvals?reason=hardship"),
            _tile("support", "Support calls today", len(support), "A certified agent calls first", to="/next-action"),
        ],
        "sections": [
            _table("review", "Next to review", [
                {"key": "customer", "label": "Customer"}, {"key": "reason", "label": "Approval for"},
                {"key": "risk", "label": "Risk", "kind": "percent"}, {"key": "summary", "label": "Why", "wrap": True}],
                [{"customer": r["golden_customer_id"], "reason": APPROVAL_NAME.get(r["approval_reason"]),
                  "risk": r["risk_score"], "summary": r["summary"], "_to": f"/cases/{r['recommendation_id']}"}
                 for r in pending[:8]], "Highest risk first. Select one to review the card and decide.",
                "Nothing is waiting for you."),
            _bars("why", "Why a support call is due", count_by(support, "support_reason"), SUPPORT_NAME),
            _bars("progs", "Programmes on the cards", dict(programmes), {}, None,
                  "Hardship options the system suggests offering first."),
        ],
    }


def _qa(con, user):
    since = AS_OF - timedelta(days=6)
    dec = decision_rows(con, since, AS_OF)
    ov = [d for d in dec if d["decision"] == "overridden"]
    by_reason = count_by(ov, "reason_code")
    safeguard = sum(v for k, v in by_reason.items() if k in SAFEGUARDS)
    rows = state_rows(con, AS_OF)
    failed = 0
    if con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'gold' AND "
                   "table_name = '_contract_results'").fetchone()[0]:
        failed = con.execute("SELECT count(*) FROM gold._contract_results WHERE NOT passed").fetchone()[0]
    return {
        "headline": "Oversight",
        "intro": "How decisions, data and access are behaving. Aggregates only: no customer cases are opened here.",
        "tiles": [
            _tile("override", "Override rate (7 days)", _pct(len(ov), len(dec)) if dec else None,
                  f"{len(ov)} of {len(dec)} decisions" if dec else "No decisions yet", to="/decision-review", kind="percent"),
            _tile("safeguard", "Safeguard overrides", safeguard, "Hardship, vulnerability or dispute missed on the card",
                  "warn" if safeguard else "ok", "/decision-review"),
            _tile("rules", "Failed data rules", failed, tone="danger" if failed else "ok", to="/status"),
            _tile("issues", "Refused or failed requests (24 h)", None, "From the access log", to="/audit?outcome=issues"),
        ],
        "sections": [
            _bars("reasons", "Overrides by reason (7 days)", by_reason, _labels(), lambda k: f"/decision-review?reason={k}",
                  "Select a reason to see who is using it."),
            _bars("mix", "Recommendation mix today", count_by(rows, "action"), ACTION_NAME, None,
                  "What the model recommended across the bank.", ACTION_ORDER),
            _bars("bands", "Risk score bands today", dict(Counter(_risk_band(r["risk_score"]) for r in rows)), {},
                  None, "A sudden shift in these bands is a prompt to check the model.",
                  {f"{i * 20}–{i * 20 + 20}%": i for i in range(5)}),
        ],
    }


def _labels() -> dict[str, str]:
    from ..nba_state import reason_labels
    return reason_labels()[0]


BUILDERS = {"agent": _agent, "senior_agent": _agent, "team_lead": _team_lead,
            "hardship_specialist": _specialist, "qa_analyst": _qa}


@router.get("/overview")
def overview(request: Request):
    with warehouse(request.app.state.settings) as con:
        user = current_user(request, con)
        rbac.require(user, "overview")
        build = BUILDERS.get(user.role)
        if build is None:
            raise ApiError(403, "forbidden", "Your role does not have access to this.")
        data = build(con, user) if have_decisions(con) else {
            "headline": "Overview", "intro": "Recommendations have not been produced for today yet.", "tiles": [],
            "sections": []}
    if user.role == "qa_analyst":  # the one headline that comes from the access log, not the warehouse
        for t in data["tiles"]:
            if t["id"] == "issues":
                n = request.app.state.access_log.issues_since(24)
                t["value"], t["tone"] = n, ("warn" if n else "ok")
    return jsonable({"as_of_date": AS_OF, "role": user.role, **data})
