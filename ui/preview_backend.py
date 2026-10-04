"""Stand-ins for P3's `trustissues.nba` and P2's `trustissues.ask`, used ONLY by the synthetic preview (ui/preview.py).

The real modules are not on this branch, so without these the queue, decision card, approvals and questions screens
would all say "not connected". They read and write the preview's own temporary DuckDB file and follow the same role
rules as the real modules (nba/review.py: who may accept, override, approve and reject; ask/access.py: who may use
which Ask path), so the screens and the server-side access checks can be tried end to end. When the real modules are
merged this file is simply not used: `ui/serve.py` never imports it.
"""
from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from tiui.nba_state import CALL_OUTCOME_LABELS, SAFEGUARD_REASONS, REASON_LABELS, count_by, decision_rows, state_rows
from tiui.rbac import CALL_ACTIONS, SUPPORT_APPROVALS

SUPERVISORS = ("team_lead", "hardship_specialist", "qa_analyst")
ACTION_ORDER = {"support_call": 0, "agent_call": 1, "sms_reminder": 2, "email_reminder": 2, "push_reminder": 2,
                "letter": 2, "wait": 3}
APPROVERS = {"vulnerability": ("team_lead", "hardship_specialist"), "hardship": ("team_lead", "hardship_specialist"),
             "high_balance": ("team_lead",), "digital_batch": ("team_lead",)}


class AccessDenied(PermissionError):
    pass


class ReviewError(ValueError):
    pass


def _staff_role(con, agent_id):
    row = con.execute("SELECT role FROM curated.agents WHERE agent_id = ?", [agent_id]).fetchone()
    return row[0] if row else None


# ------------------------------------------------------------------ nba.agent_view
def _status(con, as_of):
    return {r["recommendation_id"]: r for r in state_rows(con, as_of)}


def agent_queue(con, agent_id, as_of):
    st = _status(con, as_of)
    rows = []
    for r in st.values():
        if r["agent_id"] != agent_id:
            continue
        rows.append({"recommendation_id": r["recommendation_id"], "case_id": r["case_id"],
                     "customer": r["golden_customer_id"], "action": r["action"], "channel": r["channel"],
                     "risk_score": r["risk_score"], "expected_value": r["expected_value"],
                     "approval_reason": r["approval_reason"], "summary": r["summary"],
                     "support_reason": r["support_reason"], "approval": r["approval_decision"] or "pending",
                     "agent": r["agent_decision"] or "pending", "state": r["state"], "other_cases": 0})
    df = pd.DataFrame(rows, columns=["recommendation_id", "case_id", "customer", "action", "channel", "risk_score",
                                     "expected_value", "approval_reason", "summary", "support_reason", "approval",
                                     "agent", "state", "other_cases"])
    if df.empty:
        return df
    df["order"] = df["action"].map(ACTION_ORDER).fillna(9)
    df = df.sort_values(["order", "expected_value", "risk_score"], ascending=[True, False, False]).drop(columns="order")
    df["other_cases"] = df.groupby("customer")["recommendation_id"].transform("size") - 1
    df["todays_contact"] = ~df.duplicated("customer", keep="first")
    return df.reset_index(drop=True)


def lead_digest(con, as_of):
    rows = list(_status(con, as_of).values())
    pending = [r for r in rows if r["approval_pending"] and r["approval_reason"] != "digital_batch"]
    batch = [r for r in rows if r["approval_reason"] == "digital_batch"]
    df = pd.DataFrame([{"recommendation_id": r["recommendation_id"], "approval_reason": r["approval_reason"],
                        "risk_score": r["risk_score"], "summary": r["summary"]} for r in pending],
                      columns=["recommendation_id", "approval_reason", "risk_score", "summary"])
    over = con.execute("""
        WITH ag AS (SELECT recommendation_id, agent_id AS overridden_by, decision, reason_code, ts
                    FROM gold.nba_overrides WHERE decision IN ('accepted', 'overridden')
                    QUALIFY row_number() OVER (PARTITION BY recommendation_id ORDER BY ts DESC) = 1)
        SELECT d.recommendation_id, d.case_id, d.golden_customer_id AS customer, d.action, d.approval_reason,
               d.risk_score, json_extract_string(d.decision_card, '$.summary') AS summary,
               ag.overridden_by, ag.reason_code, ag.ts AS overridden_at
        FROM gold.nba_decisions d JOIN ag USING (recommendation_id)
        WHERE d.as_of_date = ? AND ag.decision = 'overridden' ORDER BY ag.ts DESC""", [as_of]).df()
    return {"states": count_by(rows, "state"), "overridden": over,
            "batch_pending": sum(bool(r["approval_pending"]) for r in batch), "batch_total": len(batch),
            "pending": df.sort_values(["approval_reason", "risk_score"], ascending=[True, False]) if len(df) else df}


def card(con, recommendation_id, user):
    df = con.execute("SELECT * FROM gold.nba_decisions WHERE recommendation_id = ?", [recommendation_id]).df()
    if df.empty:
        raise KeyError(recommendation_id)
    rec = df.iloc[0].to_dict()
    if not user or user["status"] != "active" or not (user["role"] in SUPERVISORS or rec["agent_id"] == user["agent_id"]):
        raise AccessDenied(recommendation_id)
    rec["card"] = json.loads(rec.pop("decision_card"))
    st = _status(con, rec["as_of_date"]).get(recommendation_id, {})
    rec["status"] = {"approval": st.get("approval_decision") or "pending", "agent": st.get("agent_decision") or "pending",
                     "completion": "completed" if st.get("completed") else "not_done", "outcome": st.get("outcome"),
                     "state": st.get("state")}
    rec["history"] = con.execute("""SELECT ts, agent_id AS decided_by, decision, reason_code FROM gold.nba_overrides
                                    WHERE recommendation_id = ? ORDER BY ts""", [recommendation_id]).df()
    return rec


def case_context(con, case_id, as_of):
    facts = con.execute("SELECT * FROM demo_src.case_facts WHERE case_id = ?", [case_id]).df()
    return {"facts": facts.iloc[0].to_dict() if len(facts) else {},
            "contacts": con.execute('SELECT "when", channel, direction, treatment, reached, promise_made, payment_taken '
                                    'FROM demo_src.contacts WHERE case_id = ? ORDER BY "when" DESC', [case_id]).df(),
            "promises": con.execute("SELECT made, amount, share_of_overdue, due, status FROM demo_src.promises "
                                    "WHERE case_id = ? ORDER BY made DESC", [case_id]).df()}


def customer_context(con, customer, as_of):
    """The person's cases (one row per case in the demo queue) and their hardship offers (none in the demo)."""
    cases = con.execute("""SELECT f.* FROM demo_src.case_facts f WHERE f.case_id IN
                           (SELECT case_id FROM gold.nba_decisions WHERE golden_customer_id = ?) ORDER BY f.case_id""",
                        [customer]).df()
    offers = con.execute("SELECT NULL::TIMESTAMP AS offered, NULL AS case_id, NULL AS programme WHERE false").df()
    return {"customer": customer, "cases": cases, "offers": offers}


# ------------------------------------------------------------------ nba.review
def _record(con, rec_id, user_id, decision, reason_code):
    from tiui.deps import AS_OF
    ts = datetime.combine(AS_OF, datetime.now().time())  # the preview lives on the business date
    con.execute("INSERT INTO gold.nba_overrides VALUES (?, ?, ?, ?, ?)", [rec_id, user_id, decision, reason_code, ts])
    return {"recommendation_id": rec_id, "agent_id": user_id, "decision": decision, "reason_code": reason_code, "ts": ts}


def record(con, rec_id, user_id, decision, reason_code=None):
    r = con.execute("SELECT agent_id, requires_approval, approval_reason, action, as_of_date FROM gold.nba_decisions "
                    "WHERE recommendation_id = ?", [rec_id]).fetchone()
    if r is None:
        raise ReviewError("No such recommendation.")
    agent_id, needs, reason, action, as_of = r
    st = _status(con, as_of).get(rec_id, {})
    if st.get("completed"):
        raise ReviewError("This call has been marked complete: nothing more can be decided on it.")
    if decision in ("approved", "rejected") and st.get("approval_decision"):
        raise ReviewError(f"This case has already been {st['approval_decision']}: an approval decision is final.")
    if decision in ("accepted", "overridden") and st.get("agent_decision") == decision:
        raise ReviewError(f"This case is already {decision}: nothing to change.")
    if decision in ("accepted", "overridden", "completed"):
        if agent_id != user_id:
            raise ReviewError("Only the agent this recommendation is assigned to can decide it.")
        if decision == "overridden" and reason_code is None:
            raise ReviewError("An override needs a reason code.")
        if decision == "completed":
            if action not in CALL_ACTIONS:
                raise ReviewError("Only a call is marked complete.")
            if reason_code not in CALL_OUTCOME_LABELS:
                raise ReviewError("A completed call needs its outcome.")
            if st.get("state") != "ready":
                raise ReviewError("A call is marked complete once it is accepted and any approval is in.")
    else:
        role = _staff_role(con, user_id)
        if not needs:
            raise ReviewError("This recommendation does not need approval.")
        if role not in APPROVERS.get(reason, ()):
            raise ReviewError(f"Your role cannot approve {str(reason).replace('_', ' ')} cases.")
        if decision == "rejected" and reason_code is None:
            raise ReviewError("A rejection needs a reason code.")
    return _record(con, rec_id, user_id, decision, reason_code)


def approve_batch(con, lead_id, as_of, rejected=None):
    if _staff_role(con, lead_id) != "team_lead":
        raise ReviewError("Only a team lead can approve the digital batch.")
    pending = [r for r in _status(con, as_of).values()
               if r["approval_reason"] == "digital_batch" and r["approval_pending"]]
    rejected = rejected or {}
    for r in pending:
        rid = r["recommendation_id"]
        if rid in rejected:
            _record(con, rid, lead_id, "rejected", rejected[rid])
        else:
            _record(con, rid, lead_id, "approved", None)
    return {"approved": len(pending) - len([p for p in pending if p["recommendation_id"] in rejected]),
            "rejected": len([p for p in pending if p["recommendation_id"] in rejected])}


def summary(con, start, end):
    rows = decision_rows(con, start, end)
    over = [r for r in rows if r["decision"] == "overridden"]
    return {"recommendations": len(_status(con, end)), "agent_decisions": len(rows),
            "override_rate": len(over) / len(rows) if rows else 0.0,
            "overrides_by_reason": count_by(over, "reason_code")}


# ------------------------------------------------------------------ ask
@dataclass
class AnswerCard:
    question: str
    answer: str
    path: str
    as_of_date: object
    refused: bool = False
    refusal_reason: str | None = None
    metric_id: str | None = None
    sql: str | None = None
    parameters: dict = field(default_factory=dict)
    certified: bool | None = None
    columns: tuple = ()
    rows: tuple = ()
    sources: list = field(default_factory=list)
    notes: list = field(default_factory=list)


@dataclass(frozen=True)
class UserContext:
    user_id: str
    role: str

PATH_ROLES = {"metric": {"team_lead", "analyst"}, "rag_corpus": {"agent", "team_lead", "analyst"},
              "rag_customer": {"agent", "team_lead"}}
POLICY = [
    (("hardship", "struggl", "afford"), "Offer a hardship programme before asking for payment. A customer with recorded "
     "hardship gets a support call from a certified agent; no payment request is made until an option has been offered.",
     ["policy:POL-COLL-004#4.2"]),
    (("vulnerab",), "Vulnerable customers are contacted by a certified agent, never by an automated reminder, and any "
     "contact needs a team lead's or hardship specialist's approval.", ["policy:POL-COLL-004#3.1"]),
    (("hour", "when", "time", "call"), "Calls and texts may be made between 09:00 and 18:00 in the customer's local "
     "time, and Sundays only where the customer has agreed. A preferred window narrows this but never widens it.",
     ["policy:POL-COLL-001#2.3"]),
    (("cease", "stop contact", "dispute", "bankrupt"), "A cease-contact request, an open dispute, insolvency or a "
     "death on file stops all collection contact until it is resolved.", ["policy:POL-COLL-002#1.1"]),
    (("promise", "ptp"), "A promise to pay under 30 days is followed up on its due date. After two broken promises the "
     "case moves to a call.", ["policy:POL-COLL-003#5.2"]),
]


def ask(question, user, *, as_of_date, con=None, **_):
    q = question.lower()
    card = lambda answer, path, **kw: AnswerCard(question, answer, path, as_of_date, **kw)  # noqa: E731
    note = "Preview assistant: canned examples, not written by the language model."
    import re
    m = re.search(r"cs-\d{4}-\d+", q)
    if m:
        if user.role not in PATH_ROLES["rag_customer"]:
            return card(f"I can't help with that. Role '{user.role}' cannot use the rag_customer path.", "refused",
                        refused=True, refusal_reason="access")
        case = m.group(0).upper()
        row = con.execute("SELECT agent_id, decision_card FROM gold.nba_decisions WHERE case_id = ?", [case]).fetchone()
        if row is None or (user.role == "agent" and row[0] != user.user_id):
            return card("Case not found or not visible.", "refused", refused=True, refusal_reason="access")
        c = json.loads(row[1])
        return card(f"{case}: {c['summary']} Recommended: {c['recommended_action'].replace('_', ' ')}"
                    f"{' by ' + c['channel'] if c.get('channel') else ''}.", "rag_customer",
                    sources=[f"timeline:{case}"], notes=[note])
    if any(w in q for w in ("how many", "count", "total", "number of", "average", "rate")):
        if user.role not in PATH_ROLES["metric"]:
            return card(f"I can't help with that. Role '{user.role}' cannot use the metric path.", "refused",
                        refused=True, refusal_reason="access")
        rows = con.execute("SELECT action, count(*) FROM gold.nba_decisions WHERE as_of_date = ? GROUP BY 1 ORDER BY 2 DESC",
                           [as_of_date]).fetchall()
        return card("\n".join(f"{a},{n}" for a, n in rows), "metric", metric_id="recommendations_by_action",
                    certified=True, columns=("treatment", "recommendations"), rows=tuple(rows),
                    sources=["gold.nba_decisions"], notes=[note])
    for words, text, sources in POLICY:
        if any(w in q for w in words):
            return card(text, "rag_corpus", sources=sources, notes=[note])
    return card("I can't tell what is being asked. Try naming a policy topic, a case such as CS-2026-100001, or a "
                "count such as how many recommendations today.", "cannot_answer", notes=[note])


# ------------------------------------------------------------------ install
def install() -> None:
    """Put the stand-ins where `from trustissues.nba import agent_view, review` will find them."""
    agent_view = types.SimpleNamespace(SUPERVISORS=SUPERVISORS, AccessDenied=AccessDenied, agent_queue=agent_queue,
                                       lead_digest=lead_digest, card=card, case_context=case_context,
                                       customer_context=customer_context)
    review = types.SimpleNamespace(
        REASON_CODES=REASON_LABELS, SAFEGUARD_REASONS=SAFEGUARD_REASONS, APPROVERS=APPROVERS, ReviewError=ReviewError,
        CALL_OUTCOMES=CALL_OUTCOME_LABELS,
        record=record, approve_batch=approve_batch, summary=summary)
    sources = types.SimpleNamespace(register=lambda con, as_of=None: None)
    nba = types.ModuleType("trustissues.nba")
    nba.agent_view, nba.review = agent_view, review
    feats = types.ModuleType("trustissues.features")
    feats.sources = sources
    service = types.ModuleType("trustissues.ask.service")
    service.ask = ask
    ask_types = types.ModuleType("trustissues.ask.types")
    ask_types.UserContext = UserContext
    access = types.ModuleType("trustissues.ask.access")
    access.case_customer_resolver = lambda con, as_of: None  # the stand-in ask() resolves cases itself
    for name, mod in {"trustissues.nba": nba, "trustissues.nba.agent_view": agent_view, "trustissues.nba.review": review,
                      "trustissues.features": feats, "trustissues.features.sources": sources,
                      "trustissues.ask.service": service, "trustissues.ask.types": ask_types,
                      "trustissues.ask.access": access}.items():
        sys.modules[name] = mod
        # `from trustissues.ask import service` reads the package's attribute, not sys.modules, once the real package
        # has been imported (in a test run that also imported the real modules), so point that at the stand-in too
        parent, _, child = name.rpartition(".")
        if parent in sys.modules and parent != "trustissues":
            setattr(sys.modules[parent], child, mod)
