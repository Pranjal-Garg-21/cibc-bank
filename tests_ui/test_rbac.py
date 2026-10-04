"""Role-based access control, end to end, on the seeded demo warehouse (the same data the preview shows).

Covers what each of the five staff roles can reach, the customers each may open, and the separation of duties:
agents decide only their own cases and approve nothing; hardship specialists approve only hardship and vulnerability;
only team leads approve the digital batch; QA sees aggregates and the audit log, never a customer's case.
"""
from __future__ import annotations

import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import preview
import preview_backend
import preview_data
from tiui.main import create_app

STAFF = {s[0]: s for s in preview_data.STAFF}
BASE = "http://127.0.0.1:8000"
STUBS = ("trustissues.nba", "trustissues.nba.agent_view", "trustissues.nba.review", "trustissues.features",
         "trustissues.features.sources", "trustissues.ask.service", "trustissues.ask.types")

REC_CALL = "REC-20260928-CS-2026-100001"       # agent_call, DEMO-A1, no approval
REC_BATCH = "REC-20260928-CS-2026-100002"      # sms_reminder, digital batch, no agent
REC_HARDSHIP = "REC-20260928-CS-2026-100003"   # support_call, DEMO-A1, hardship approval
REC_BIG = "REC-20260928-CS-2026-100123"        # agent_call, high balance approval (seeded; checked below)


@pytest.fixture
def app(tmp_path):
    saved = {n: sys.modules.get(n) for n in STUBS}
    settings = replace(preview.build_preview(tmp_path), allowed_hosts=("localhost", "127.0.0.1", "testserver"))
    preview_backend.install()
    try:
        yield create_app(settings)
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


def sign_in(app, sid: str, reauthenticate: bool = True):
    """A fresh browser session for this staff member: (client, csrf token)."""
    s = STAFF[sid]
    c = TestClient(app, base_url=BASE)
    app.state.limiter._hits.clear()  # the sign-in limit is tested elsewhere
    r = c.post("/api/session/login", json={"role": preview_data.SIGN_IN_TAB[s[3]], "staff_id": sid, "password": s[-1]})
    assert r.status_code == 200, r.text
    csrf = c.get("/api/session/me").json()["csrf_token"]
    if reauthenticate:
        r = c.post("/api/session/reauthenticate", json={"password": s[-1]}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        csrf = r.json()["csrf_token"]
    return c, csrf


def allowed(me: dict) -> set[str]:
    return {k for k, v in me["can"].items() if v}


# ------------------------------------------------------------------------------------------ navigation
@pytest.mark.parametrize("sid, expected", [
    ("DEMO-A1", {"overview", "my_queue", "next_action", "case_view", "decide", "ask", "ask_case"}),
    ("DEMO-A2", {"overview", "my_queue", "next_action", "case_view", "decide", "ask", "ask_case"}),
    ("DEMO-L1", {"overview", "next_action", "case_view", "approvals", "approve_batch", "manage_agents", "identity_review",
                 "decision_review", "status", "tables", "ask", "ask_case", "ask_portfolio"}),
    ("DEMO-H1", {"overview", "next_action", "case_view", "approvals", "ask", "ask_case"}),
    ("DEMO-Q1", {"overview", "identity_review", "decision_review", "status", "audit_log", "ask", "ask_portfolio"}),
])
def test_each_role_gets_only_its_own_features(app, sid, expected):
    c, _ = sign_in(app, sid, reauthenticate=False)
    assert allowed(c.get("/api/session/me").json()) == expected


def test_approval_kinds_by_role(app):
    kinds = {sid: sign_in(app, sid, False)[0].get("/api/session/me").json()["approval_kinds"]
             for sid in ("DEMO-A1", "DEMO-L1", "DEMO-H1", "DEMO-Q1")}
    assert kinds["DEMO-A1"] == [] and kinds["DEMO-Q1"] == []
    assert kinds["DEMO-H1"] == ["vulnerability", "hardship"]
    assert kinds["DEMO-L1"] == ["vulnerability", "hardship", "high_balance", "digital_batch"]


# ------------------------------------------------------------------------------------------ route matrix
ENDPOINTS = ["/api/overview", "/api/queue/mine", "/api/queue/digest", "/api/next-action/customers",
             "/api/oversight/decisions", "/api/oversight/audit", "/api/status", "/api/identity/queue",
             "/api/team/agents", "/api/review/summary", "/api/review/reasons"]
#                A1   L1   H1   Q1
MATRIX = {
    "/api/overview":              (200, 200, 200, 200),
    "/api/queue/mine":            (200, 403, 403, 403),
    "/api/queue/digest":          (403, 200, 200, 403),
    "/api/next-action/customers": (200, 200, 200, 403),
    "/api/oversight/decisions":   (403, 200, 403, 200),
    "/api/oversight/audit":       (403, 403, 403, 200),
    "/api/status":                (403, 200, 403, 200),
    "/api/identity/queue":        (403, 200, 403, 200),
    "/api/team/agents":           (403, 200, 403, 403),
    "/api/review/summary":        (403, 200, 403, 200),
    "/api/review/reasons":        (200, 200, 200, 403),
}


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_route_access_matrix(app, endpoint):
    got = tuple(sign_in(app, sid, False)[0].get(endpoint).status_code
                for sid in ("DEMO-A1", "DEMO-L1", "DEMO-H1", "DEMO-Q1"))
    assert got == MATRIX[endpoint], endpoint


def test_everything_needs_a_session(app):
    c = TestClient(app, base_url=BASE)
    for e in (*ENDPOINTS, "/api/next-action/customers/G-0100001", f"/api/cases/{REC_CALL}"):
        assert c.get(e).status_code == 401, e


def test_wrong_tab_fails_like_a_wrong_password(app):
    c = TestClient(app, base_url=BASE)
    for sid, tab in (("DEMO-H1", "agent"), ("DEMO-Q1", "team_lead"), ("DEMO-L1", "qa_analyst"), ("DEMO-A1", "hardship_specialist")):
        app.state.limiter._hits.clear()
        wrong_tab = c.post("/api/session/login", json={"role": tab, "staff_id": sid, "password": STAFF[sid][-1]})
        app.state.limiter._hits.clear()
        wrong_pw = c.post("/api/session/login", json={"role": preview_data.SIGN_IN_TAB[STAFF[sid][3]], "staff_id": sid,
                                                      "password": "not-the-password-1!"})
        assert wrong_tab.status_code == wrong_pw.status_code == 401
        assert wrong_tab.json()["message"] == wrong_pw.json()["message"]


# ------------------------------------------------------------------------------------------ which customers
def customers(c) -> dict[str, list[dict]]:
    out = {}
    for row in c.get("/api/next-action/customers").json()["customers"]:
        out[row["customer_id"]] = c.get(f"/api/next-action/customers/{row['customer_id']}").json()["recommendations"]
    return out


def test_agent_sees_only_own_customers(app):
    a1, _ = sign_in(app, "DEMO-A1", False)
    a2, _ = sign_in(app, "DEMO-A2", False)
    mine, theirs = customers(a1), customers(a2)
    assert mine and theirs
    assert all(r["agent_id"] == "DEMO-A1" for recs in mine.values() for r in recs)
    assert all(r["agent_id"] == "DEMO-A2" for recs in theirs.values() for r in recs)
    assert not set(mine) & set(theirs)
    other = next(iter(theirs))
    assert a1.get(f"/api/next-action/customers/{other}").status_code == 404
    assert a1.get(f"/api/next-action/customers?q={other}").json()["customers"] == []


def test_specialist_sees_only_hardship_vulnerability_and_support_calls(app):
    h, _ = sign_in(app, "DEMO-H1", False)
    seen = customers(h)
    assert seen
    for recs in seen.values():
        assert all(r["approval_reason"] in ("hardship", "vulnerability") or r["action"] == "support_call" for r in recs)
    assert h.get("/api/next-action/customers/G-0100002").status_code == 404   # a digital-batch SMS
    assert h.get(f"/api/cases/{REC_BATCH}").status_code == 404
    assert h.get(f"/api/cases/{REC_HARDSHIP}").status_code == 200


def test_team_lead_sees_all_customers(app):
    l1, _ = sign_in(app, "DEMO-L1", False)
    assert len(customers(l1)) > len(customers(sign_in(app, "DEMO-A1", False)[0]))


def test_qa_never_reaches_a_customer(app):
    q, csrf = sign_in(app, "DEMO-Q1")
    assert q.get("/api/next-action/customers/G-0100001").status_code == 403
    assert q.get(f"/api/cases/{REC_CALL}").status_code == 403
    assert q.get("/api/queue/mine").status_code == 403
    for body in ({"decision": "accepted"}, {"decision": "approved"}):
        assert q.post(f"/api/review/{REC_CALL}", json=body, headers={"X-CSRF-Token": csrf}).status_code == 403
    assert q.post("/api/review-batch", json={}, headers={"X-CSRF-Token": csrf}).status_code == 403


def test_a_case_you_may_not_see_looks_like_one_that_does_not_exist(app):
    a2, _ = sign_in(app, "DEMO-A2", False)
    hidden, missing = a2.get(f"/api/cases/{REC_CALL}"), a2.get("/api/cases/REC-20260928-NOPE")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["message"] == missing.json()["message"]


def test_qa_overview_carries_no_customer_identifiers(app):
    q, _ = sign_in(app, "DEMO-Q1", False)
    text = q.get("/api/overview").text
    assert "G-0" not in text and "CS-2026" not in text and "REC-" not in text


# ------------------------------------------------------------------------------------------ separation of duties
def post(c, csrf, rec, **body):
    return c.post(f"/api/review/{rec}", json=body, headers={"X-CSRF-Token": csrf})


def test_agent_decides_own_cases_only_and_approves_nothing(app):
    a1, c1 = sign_in(app, "DEMO-A1")
    a2, c2 = sign_in(app, "DEMO-A2")
    assert post(a1, c1, REC_CALL, decision="approved").status_code == 403      # agents give no approvals
    assert post(a2, c2, REC_CALL, decision="accepted").status_code == 409      # not their recommendation
    assert post(a1, c1, REC_CALL, decision="overridden").status_code == 409    # an override needs a reason
    assert post(a1, c1, REC_CALL, decision="accepted").status_code == 200


def test_specialist_approves_hardship_but_not_batch_or_high_balance(app):
    h, csrf = sign_in(app, "DEMO-H1")
    assert post(h, csrf, REC_BATCH, decision="approved").status_code == 409
    assert post(h, csrf, REC_BIG, decision="approved").status_code == 409
    assert post(h, csrf, REC_HARDSHIP, decision="approved").status_code == 200
    assert post(h, csrf, REC_CALL, decision="accepted").status_code == 403     # specialists are not call agents
    assert h.post("/api/review-batch", json={}, headers={"X-CSRF-Token": csrf}).status_code == 403


def test_only_a_team_lead_approves_the_batch(app):
    l1, csrf = sign_in(app, "DEMO-L1")
    before = next(t for t in l1.get("/api/overview").json()["tiles"] if t["id"] == "batch")["value"]
    assert before > 0
    assert l1.post("/api/review-batch", json={}, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert next(t for t in l1.get("/api/overview").json()["tiles"] if t["id"] == "batch")["value"] == 0


def test_approvals_need_a_fresh_password_check(app):
    h, csrf = sign_in(app, "DEMO-H1", reauthenticate=False)
    r = post(h, csrf, REC_HARDSHIP, decision="approved")
    assert r.status_code == 403 and r.json()["code"] == "reauthentication_required"


def test_specialist_digest_hides_the_batch_and_other_approval_types(app):
    d = sign_in(app, "DEMO-H1", False)[0].get("/api/queue/digest").json()
    reasons = {row[d["pending"]["columns"].index("approval_reason")] for row in d["pending"]["rows"]}
    assert reasons and reasons <= {"hardship", "vulnerability"}
    assert d["batch_total"] == 0 and d["batch_pending"] == 0 and d["states"] == {}
    lead = sign_in(app, "DEMO-L1", False)[0].get("/api/queue/digest").json()
    assert lead["batch_total"] > 0


def test_pending_approval_does_not_depend_on_the_agent_having_acted(app):
    l1, _ = sign_in(app, "DEMO-L1", False)
    tiles = {t["id"]: t["value"] for t in l1.get("/api/overview").json()["tiles"]}
    assert tiles["approvals"] > 0   # hardship, vulnerability and high-balance calls still waiting for their agent


# ------------------------------------------------------------------------------------------ overviews
def test_agent_overview_moves_when_a_decision_is_made(app):
    a1, csrf = sign_in(app, "DEMO-A1")
    view = lambda: a1.get("/api/overview").json()  # noqa: E731
    waiting = lambda d: next(t for t in d["tiles"] if t["id"] == "waiting")["value"]  # noqa: E731
    d = view()
    before = waiting(d)
    rec = next(s for s in d["sections"] if s["id"] == "next")["rows"][0]["_to"].rsplit("/", 1)[1]  # a case waiting on A1
    assert post(a1, csrf, rec, decision="accepted").status_code == 200
    assert waiting(view()) == before - 1


def test_overview_content_is_role_specific(app):
    ids = {sid: {s["id"] for s in sign_in(app, sid, False)[0].get("/api/overview").json()["sections"]}
           for sid in ("DEMO-A1", "DEMO-L1", "DEMO-H1", "DEMO-Q1")}
    assert "next" in ids["DEMO-A1"] and "team" in ids["DEMO-L1"] and "review" in ids["DEMO-H1"] and "reasons" in ids["DEMO-Q1"]
    assert not ids["DEMO-A1"] & {"team", "kinds", "reasons"} and not ids["DEMO-H1"] & {"team", "reasons"}


# ------------------------------------------------------------------------------------------ oversight
def test_decision_review_scope(app):
    assert sign_in(app, "DEMO-L1", False)[0].get("/api/oversight/decisions").json()["scope"] == "team"
    q = sign_in(app, "DEMO-Q1", False)[0].get("/api/oversight/decisions?days=30").json()
    assert q["scope"] == "bank" and q["days"] == 30 and {a["agent_id"] for a in q["by_agent"]} >= {"DEMO-A1", "DEMO-A3"}
    assert any(a["outlier"] for a in q["by_agent"])  # the seeded agent who overrides far more than the rest


def test_audit_log_is_qa_only_filterable_and_itself_logged(app):
    q, _ = sign_in(app, "DEMO-Q1", False)
    sign_in(app, "DEMO-A1", False)[0].get("/api/oversight/audit")  # refused, and recorded as a refusal
    first = q.get("/api/oversight/audit?hours=1").json()
    assert first["total"] > 0 and {"sign_in"} <= set(first["actions"])
    again = q.get("/api/oversight/audit?hours=1&action=view_audit_log").json()
    assert again["events"] and all(e["action"] == "view_audit_log" and e["user_id"] == "DEMO-Q1" for e in again["events"])
    issues = q.get("/api/oversight/audit?hours=1&outcome=issues").json()
    assert issues["events"] and all(e["outcome"] != "ok" for e in issues["events"])
    assert q.get("/api/oversight/audit?hours=1&user=DEMO-Q1").json()["events"]


@pytest.mark.parametrize("query", ["q=%27%3B%20DROP%20TABLE%20x", "user=a%2Fb", "action=%3Cscript%3E", "hours=0", "hours=9999", "limit=100000"])
def test_audit_filters_reject_odd_input(app, query):
    q, _ = sign_in(app, "DEMO-Q1", False)
    assert q.get(f"/api/oversight/audit?{query}").status_code == 422


# ------------------------------------------------------------------------------------------ ask roles
def test_questions_follow_the_role(app):
    def ask(sid, text):
        c, csrf = sign_in(app, sid, False)
        return c.post("/api/ask", json={"question": text}, headers={"X-CSRF-Token": csrf}).json()
    assert ask("DEMO-A1", "how many recommendations today")["path"] == "refused"      # no portfolio numbers for agents
    assert ask("DEMO-H1", "how many recommendations today")["path"] == "refused"
    assert ask("DEMO-L1", "how many recommendations today")["path"] == "metric"
    assert ask("DEMO-Q1", "how many recommendations today")["path"] == "metric"
    assert ask("DEMO-A1", "what is the policy for hardship")["path"] == "rag_corpus"
    assert ask("DEMO-A1", "what is happening on CS-2026-100001")["path"] == "rag_customer"
    assert ask("DEMO-A2", "what is happening on CS-2026-100001")["refused"] is True   # someone else's case
    assert ask("DEMO-Q1", "what is happening on CS-2026-100001")["refused"] is True   # QA never reads a case
