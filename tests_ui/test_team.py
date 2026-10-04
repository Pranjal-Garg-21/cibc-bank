"""Two sign-in roles, and agent passwords set by their team lead."""
from __future__ import annotations

from uikit import TEST_PASSWORD, sign_in

AGENT_PW = "agent-pass-" + TEST_PASSWORD[-8:]


def login(c, staff_id, password, role):
    return c.post("/api/session/login", json={"role": role, "staff_id": staff_id, "password": password})


def test_role_tab_must_match_the_account(client):
    client.app.state.limiter.limits["login"] = []  # many sign-ins in one test; the per-address limit is tested elsewhere
    assert login(client, "L1", TEST_PASSWORD, "agent").status_code == 401       # a lead on the Agent tab
    assert login(client, "A1", TEST_PASSWORD, "team_lead").status_code == 401   # an agent on the Team lead tab
    r = login(client, "A1", TEST_PASSWORD, "team_lead")
    assert r.json()["message"] == login(client, "A1", "wrong-password-1", "agent").json()["message"]  # same message
    assert login(client, "A1", TEST_PASSWORD, "agent").status_code == 200
    assert login(client, "L1", TEST_PASSWORD, "team_lead").status_code == 200


def test_other_roles_cannot_sign_in(client):
    for tab in ("agent", "team_lead"):
        assert login(client, "Q1", TEST_PASSWORD, tab).status_code == 401  # QA analyst: no access to this app
    assert client.post("/api/session/login", json={"role": "admin", "staff_id": "L1", "password": TEST_PASSWORD}).status_code == 422


def test_lead_sees_only_their_own_agents(client):
    sign_in(client, "L1")
    r = client.get("/api/team/agents")
    assert r.status_code == 200
    body = r.json()
    assert [a["agent_id"] for a in body["agents"]] == ["A1", "A2"]  # not A3 (L2's), not X1 (terminated)
    assert all("hash" not in a and "password" not in a for a in body["agents"])
    assert body["agents"][0]["has_account"] is True


def test_agents_cannot_manage_accounts(client):
    csrf = sign_in(client, "A1")
    assert client.get("/api/team/agents").status_code == 403
    r = client.post("/api/team/agents/A2/password", json={"password": AGENT_PW}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403


def test_lead_sets_an_agents_password_and_it_works(client, ui_settings):
    csrf = sign_in(client, "L1")
    r = client.post("/api/team/agents/A2/password", json={"password": AGENT_PW}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["set_by"] == "L1" and "password" not in r.json()
    assert AGENT_PW not in ui_settings.accounts_file.read_text(encoding="utf-8")  # stored hashed
    assert login(client, "A2", TEST_PASSWORD, "agent").status_code == 401  # old password replaced
    assert login(client, "A2", AGENT_PW, "agent").status_code == 200


def test_lead_cannot_touch_another_teams_agent_or_a_lead(client):
    csrf = sign_in(client, "L1")
    h = {"X-CSRF-Token": csrf}
    assert client.post("/api/team/agents/A3/password", json={"password": AGENT_PW}, headers=h).status_code == 404
    assert client.post("/api/team/agents/L2/password", json={"password": AGENT_PW}, headers=h).status_code == 404
    assert client.post("/api/team/agents/X1/password", json={"password": AGENT_PW}, headers=h).status_code == 404
    assert client.post("/api/team/agents/A3/revoke", json={}, headers=h).status_code == 404


def test_weak_password_refused(client):
    csrf = sign_in(client, "L1")
    r = client.post("/api/team/agents/A1/password", json={"password": "short"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422 and r.json()["code"] == "weak_password"


def test_agent_has_no_way_to_change_their_password(client):
    sign_in(client, "A1")
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert not any("password" in p and "team" not in p for p in paths)


def test_revoking_signs_the_agent_out(make_client):
    from fastapi.testclient import TestClient
    agent = make_client()
    lead = TestClient(agent.app)  # a second browser on the same server
    sign_in(agent, "A1")
    assert agent.get("/api/session/me").status_code == 200
    csrf = sign_in(lead, "L1")
    r = lead.post("/api/team/agents/A1/revoke", json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["removed"] is True
    assert agent.get("/api/session/me").status_code == 401
    assert login(agent, "A1", TEST_PASSWORD, "agent").status_code == 401
