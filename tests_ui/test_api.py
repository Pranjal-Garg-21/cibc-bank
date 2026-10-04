"""UI API behaviour on a tiny warehouse. NBA and Ask modules are replaced by fakes with the real signatures, so these
tests pass on main before P2/P3 are merged; tests_ui/test_integration.py runs the real ones when they are installed.
"""
from __future__ import annotations

import csv
import sys
import types

import pandas as pd
import pytest

from uikit import TEST_PASSWORD, PAIR_1, PAIR_2, PAIR_3, sign_in


def test_sign_in_page_reveals_no_staff(client):
    opts = client.get("/api/session/options").json()
    assert set(opts) == {"sign_in_configured", "demo", "as_of_date", "idle_minutes", "mfa_required", "shared_access"}
    assert opts["sign_in_configured"] is True
    assert client.get("/api/session/staff").status_code == 404  # there is no staff directory


def test_me_lists_permissions(client):
    sign_in(client, "L1")
    me = client.get("/api/session/me").json()
    assert me["role"] == "team_lead" and me["ask_role"] == "team_lead"
    assert me["site"] == "TOR" and me["signed_in_at"].endswith("+00:00")
    assert me["can"]["identity_review"] is True and me["can"]["approvals"] is True and me["can"]["manage_agents"] is True
    sign_in(client, "A1")
    can = client.get("/api/session/me").json()["can"]
    assert can["identity_review"] is False and can["manage_agents"] is False and can["my_queue"] is True
    assert set(me["features"]) == {"queue", "ask", "identity", "status"}


def test_identity_queue_and_pair(client):
    sign_in(client, "L1")
    q = client.get("/api/identity/queue").json()
    assert q["total"] == 3 and q["counts_by_reason"] == {"ambiguous": 1, "grey_zone_no_llm": 1, "splink_ambiguous": 1}
    no_verdict = next(i for i in q["items"] if i["pair_id"] == PAIR_3)
    assert no_verdict["llm_available"] is False and no_verdict["llm_match"] is None
    p = client.get(f"/api/identity/pairs/{PAIR_1}").json()
    phones = next(f["value"] for f in p["record_a"] if f["field"] == "phone")
    assert phones == "•••• 0123, •••• 0999"  # never the full number
    assert p["llm"] == {"available": True, "match": True, "probability": 0.9, "reason": "Same person"}
    p2 = client.get(f"/api/identity/pairs/{PAIR_2}").json()
    assert p2["llm"]["available"] is False and p2["llm"]["reason"] is None  # error text never shown


def test_identity_needs_reviewer_role(client):
    sign_in(client, "A1")
    assert client.get("/api/identity/queue").status_code == 403


def test_identity_decision_appends_csv(client, ui_settings):
    csrf = sign_in(client, "L1")
    r = client.post("/api/identity/decisions", json={"pair_id": PAIR_2, "decision": "no_match", "note": "=cmd|x\nhi"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    with open(ui_settings.decisions_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == [{"node_a": "-crm|2", "node_b": "card|3", "decision": "no_match", "reviewer": "L1",
                     "decided_ts": rows[0]["decided_ts"], "evidence_key": "=ek2", "note": "'=cmd|x hi"}]
    # the pair leaves the open queue until the next identity run
    assert PAIR_2 not in [i["pair_id"] for i in client.get("/api/identity/queue").json()["items"]]
    assert client.get("/api/identity/queue", params={"include_decided": True}).json()["total"] == 3


def test_identity_decision_matches_backend_format(client, ui_settings):
    """The CSV the UI writes has exactly the header the identity build reads (review_queue.FIELDS)."""
    from trustissues.factory.identity.review_queue import FIELDS
    csrf = sign_in(client, "L1")
    client.post("/api/identity/decisions", json={"pair_id": PAIR_1, "decision": "match"}, headers={"X-CSRF-Token": csrf})
    with open(ui_settings.decisions_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == FIELDS
    assert rows[0]["decision"] in {"match", "no_match", "unresolved"} and rows[0]["evidence_key"] == "ek1"


def test_identity_decision_validates_input(client):
    csrf = sign_in(client, "L1")
    h = {"X-CSRF-Token": csrf}
    assert client.post("/api/identity/decisions", json={"pair_id": PAIR_1, "decision": "maybe"}, headers=h).status_code == 422
    assert client.post("/api/identity/decisions", json={"pair_id": "x", "decision": "match"}, headers=h).status_code == 422
    assert client.post("/api/identity/decisions", json={"pair_id": "c" * 32, "decision": "match"}, headers=h).status_code == 404


def test_status_and_reports(client):
    sign_in(client, "L1")
    s = client.get("/api/status").json()
    assert s["publish"]["published"] is True and s["rules"][0]["rule_id"] == "Q1"
    assert s["reports"][0]["title"] == "Data quality"
    assert "All good" in client.get("/api/status/reports/data_quality_report.md").json()["markdown"]
    assert client.get("/api/status/reports/..%2Fsettings.yaml").status_code == 404
    assert client.get("/api/status/reports/nope.md").status_code == 404


def test_agents_get_freshness_not_data_quality(client):
    sign_in(client, "A1")
    assert client.get("/api/session/me").json()["can"]["status"] is False
    assert client.get("/api/status").status_code == 403
    assert client.get("/api/status/reports/data_quality_report.md").status_code == 403
    assert client.get("/api/status/freshness").json()["ok"] is True


# --- NBA adapters with fakes ---------------------------------------------------------------------------------

@pytest.fixture
def fake_nba(monkeypatch):
    calls = {}

    class AccessDenied(PermissionError):
        pass

    class ReviewError(ValueError):
        pass

    def card(con, rec_id, user):
        if rec_id == "R404":
            raise KeyError(rec_id)
        rec = {"recommendation_id": rec_id, "agent_id": "A1", "case_id": "C1", "requires_approval": True,
               "approval_reason": "hardship", "card": {"summary": "Call"}, "first_name": "ANNA",
               "history": pd.DataFrame({"ts": [pd.Timestamp("2026-10-01")], "decision": ["accepted"]})}
        if rec_id == "R-READY":  # a call accepted and cleared: its agent marks it complete
            rec.update(action="agent_call", requires_approval=False, approval_reason=None,
                       status={"agent": "accepted", "approval": "not_needed", "completion": "not_done", "state": "ready"})
        if user["role"] not in ("team_lead",) and user["agent_id"] != rec["agent_id"]:
            raise AccessDenied("no")
        return rec

    def record(con, rec_id, user_id, decision, reason_code=None):
        if decision == "overridden" and not reason_code:
            raise ReviewError("An override needs a reason code.")
        calls["record"] = (rec_id, user_id, decision, reason_code)
        return {"recommendation_id": rec_id, "decision": decision}

    agent_view = types.SimpleNamespace(
        SUPERVISORS=("team_lead", "hardship_specialist", "qa_analyst"), AccessDenied=AccessDenied, card=card,
        agent_queue=lambda con, a, d: pd.DataFrame({"recommendation_id": ["R1"], "action": ["agent_call"]}),
        lead_digest=lambda con, d: {"states": {"ready": 1}, "batch_pending": 0, "batch_total": 0,
                                    "pending": pd.DataFrame()},
        case_context=lambda con, c, d: {"facts": {"case_id": c}, "contacts": pd.DataFrame(), "promises": pd.DataFrame()})
    review = types.SimpleNamespace(
        REASON_CODES={"other": "Other", "dispute_raised": "Dispute"}, SAFEGUARD_REASONS=("dispute_raised",),
        CALL_OUTCOMES={"no_answer": "No answer"},
        APPROVERS={"hardship": ("team_lead", "hardship_specialist"), "digital_batch": ("team_lead",)},
        ReviewError=ReviewError, record=record,
        approve_batch=lambda con, lead, d, rej: {"approved": 3, "rejected": len(rej or {})},
        summary=lambda con, a, b: {"recommendations": 1})
    sources = types.SimpleNamespace(register=lambda con, as_of=None: None)
    nba = types.ModuleType("trustissues.nba")
    nba.agent_view, nba.review = agent_view, review
    feats = types.ModuleType("trustissues.features")
    feats.sources = sources
    monkeypatch.setitem(sys.modules, "trustissues.nba", nba)
    monkeypatch.setitem(sys.modules, "trustissues.nba.agent_view", agent_view)
    monkeypatch.setitem(sys.modules, "trustissues.nba.review", review)
    monkeypatch.setitem(sys.modules, "trustissues.features", feats)
    monkeypatch.setitem(sys.modules, "trustissues.features.sources", sources)
    return calls


def test_queue_and_card(client, fake_nba):
    sign_in(client, "A1")
    q = client.get("/api/queue/mine").json()
    assert q["queue"]["rows"] == [["R1", "agent_call"]]
    c = client.get("/api/cases/R1").json()
    assert c["recommendation"]["card"] == {"summary": "Call"}
    assert c["recommendation"]["first_name"] == "***"  # second-line scrub of sensitive columns
    assert c["allowed"] == {"agent_decision": True, "complete": False, "approval": False}
    assert client.get("/api/cases/R-READY").json()["allowed"] == {"agent_decision": True, "complete": True,
                                                                  "approval": False}
    assert client.get("/api/cases/R404").status_code == 404
    assert client.get("/api/queue/digest").status_code == 403


def test_card_not_visible_to_other_agent_looks_like_missing(client, fake_nba):
    sign_in(client, "A2")
    other, missing = client.get("/api/cases/R1"), client.get("/api/cases/R404")
    assert other.status_code == missing.status_code == 404
    assert other.json()["message"] == missing.json()["message"]


def test_review_writes_go_through_backend(client, fake_nba):
    csrf = sign_in(client, "A1")
    h = {"X-CSRF-Token": csrf}
    r = client.post("/api/review/R1", json={"decision": "overridden"}, headers=h)
    assert r.status_code == 409 and "reason code" in r.json()["message"]
    assert client.post("/api/review/R1", json={"decision": "overridden", "reason_code": "bogus"}, headers=h).status_code == 422
    assert client.post("/api/review/R1", json={"decision": "accepted"}, headers=h).status_code == 200
    assert fake_nba["record"] == ("R1", "A1", "accepted", None)  # user id comes from the session, not the body
    # a completed call carries its outcome, from its own list (an override reason is not an outcome)
    assert client.post("/api/review/R1", json={"decision": "completed", "reason_code": "other"}, headers=h).status_code == 422
    assert client.post("/api/review/R1", json={"decision": "completed", "reason_code": "no_answer"}, headers=h).status_code == 200
    assert fake_nba["record"] == ("R1", "A1", "completed", "no_answer")


def test_batch_only_for_team_lead(client, fake_nba):
    csrf = sign_in(client, "A1")
    assert client.post("/api/review-batch", json={"password": TEST_PASSWORD}, headers={"X-CSRF-Token": csrf}).status_code == 403
    csrf = sign_in(client, "L1")
    r = client.post("/api/review-batch", json={"password": TEST_PASSWORD, "rejected": {"R2": "other"}},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["result"] == {"approved": 3, "rejected": 1}


def test_batch_needs_the_password_every_time(client, fake_nba):
    """D38: a re-authentication a moment ago (sign_in renews it) does not stand in for the batch's own password."""
    csrf = sign_in(client, "L1")
    h = {"X-CSRF-Token": csrf}
    assert client.post("/api/review-batch", json={}, headers=h).status_code == 422          # no password: refused
    r = client.post("/api/review-batch", json={"password": "not-the-password-1!"}, headers=h)
    assert r.status_code == 403 and r.json()["code"] == "verification_failed"
    for _ in range(2):  # a second batch inside the old 300 s window still asks
        assert client.post("/api/review-batch", json={"password": TEST_PASSWORD}, headers=h).status_code == 200


def test_queue_not_connected_without_backend(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "trustissues.nba", None)
    monkeypatch.setitem(sys.modules, "trustissues.nba.agent_view", None)
    sign_in(client, "A1")
    r = client.get("/api/queue/mine")
    assert r.status_code == 501 and r.json()["code"] == "not_connected"


# --- Ask adapter with a fake ---------------------------------------------------------------------------------

@pytest.fixture
def fake_ask(monkeypatch):
    from dataclasses import dataclass, field
    seen = {}

    @dataclass(frozen=True)
    class UserContext:
        user_id: str
        role: str

    @dataclass
    class AnswerCard:
        question: str
        answer: str
        path: str
        as_of_date: object
        refused: bool = False
        metric_id: str | None = None
        sql: str | None = None
        columns: tuple = ()
        rows: tuple = ()
        notes: list = field(default_factory=list)

    def ask(question, user, *, as_of_date, con=None, **kw):
        seen["user"], seen["kw"] = user, kw
        if "card" in seen:  # a test may supply the card's fields
            return AnswerCard(question, as_of_date=as_of_date, **seen["card"])
        return AnswerCard(question, "42 cases", "metric", as_of_date, metric_id="m1", columns=("province", "first_name", "cases"),
                          rows=(("ON", "ANNA", 42),))

    service = types.SimpleNamespace(ask=ask)
    t = types.SimpleNamespace(UserContext=UserContext)
    pkg = types.ModuleType("trustissues.ask")
    pkg.service, pkg.types = service, t
    monkeypatch.setitem(sys.modules, "trustissues.ask", pkg)
    monkeypatch.setitem(sys.modules, "trustissues.ask.service", service)
    monkeypatch.setitem(sys.modules, "trustissues.ask.types", t)
    return seen


def test_ask_maps_role_and_masks_rows(client, fake_ask):
    csrf = sign_in(client, "L1")
    r = client.post("/api/ask", json={"question": "How many cases by province?"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    body = r.json()
    assert fake_ask["user"].role == "team_lead" and fake_ask["user"].user_id == "L1"
    assert "llm" not in fake_ask["kw"] and "rag" not in fake_ask["kw"]  # no LLM unless UI_ASK_LLM=1
    assert "ANNA" not in str(body["rows"])
    assert body["path"] == "metric" and body["answer"] == "42 cases"


PII = "Call 416-555-0123 or anna.b@example.com; DOB 1980-01-02; card 4111 1111 1111 1111"


def test_ask_scrubs_identifiers_from_answer_text_notes_and_sql(client, fake_ask):
    """Customer-history and generated answers are free text: the column masking never sees them (review item 4)."""
    fake_ask["card"] = dict(answer=f"The customer said: {PII}. Agreed $110 on 2026-09-25 for CS-2026-465948.",
                            path="rag_customer", notes=[f"From the call: {PII}"],
                            sql="SELECT * FROM t WHERE phone = '416-555-0123'")
    csrf = sign_in(client, "A1")
    body = client.post("/api/ask", json={"question": "what did they say"}, headers={"X-CSRF-Token": csrf}).json()
    text = " ".join([body["answer"], *body["notes"], body["sql"]])
    for raw in ("416-555-0123", "anna.b@example.com", "1980-01-02", "4111 1111 1111 1111"):
        assert raw not in text, raw
    assert "[PHONE]" in body["answer"] and "[EMAIL]" in body["notes"][0]
    assert "$110 on 2026-09-25 for CS-2026-465948" in body["answer"]  # amounts, dates and case IDs stay readable


def test_ask_does_not_resend_the_table_as_csv_text(client, fake_ask):
    """ask() writes the rows as CSV into `answer`; that copy would bypass column masking, so only the table is sent."""
    fake_ask["card"] = dict(answer="summary: cases moved from 1 to 2\nprovince,first_name,cases\nON,ANNA,42",
                            path="sql", columns=("province", "first_name", "cases"), rows=(("ON", "ANNA", 42),))
    csrf = sign_in(client, "L1")
    body = client.post("/api/ask", json={"question": "cases by name"}, headers={"X-CSRF-Token": csrf}).json()
    assert body["answer"] == "Cases moved from 1 to 2"
    assert "ANNA" not in str(body)


def test_ask_validates_and_limits(client, fake_ask):
    csrf = sign_in(client, "A1")
    h = {"X-CSRF-Token": csrf}
    assert client.post("/api/ask", json={"question": "x" * 501}, headers=h).status_code == 422
    codes = [client.post("/api/ask", json={"question": "count cases"}, headers=h).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_case_facts_table_is_shown_not_blanked(client, fake_ask):
    """ask/case_facts.py returns fixed (field, value) pairs: column masking would turn every cell into ***."""
    fake_ask["card"] = dict(answer="Case CS-1. Queue: mid_stage.", path="case_facts", columns=("field", "value"),
                            rows=(("queue", "mid_stage"), ("last_contact", "call to 416-555-0123")))
    csrf = sign_in(client, "A1")
    body = client.post("/api/ask", json={"question": "queue of case CS-1"}, headers={"X-CSRF-Token": csrf}).json()
    assert body["rows"][0] == ["queue", "mid_stage"]
    assert "416-555-0123" not in str(body["rows"])  # values still go through the text scrubber


def test_customer_names_in_free_text_follow_the_full_name_column_rule():
    from tiui.security import mask_names
    t = "The customer (Jeanne Andersen) said JEANNE was laid off; Ms. Andersen agreed. Andersenville stays."
    assert mask_names(t, ["JEANNE ANDERSEN"], "agent") == \
        "The customer (J*** A***) said J*** was laid off; Ms. A*** agreed. Andersenville stays."
    assert "Jeanne" not in mask_names(t, ["JEANNE ANDERSEN"], "team_lead")
    assert mask_names(t, [], "agent") == t
