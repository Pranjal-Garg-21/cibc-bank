"""Adversarial tests for MFA, credential changes, fresh authentication and session lifecycle."""
from __future__ import annotations

import json
import stat
from dataclasses import replace

import duckdb
import pyotp
import pytest
from fastapi.testclient import TestClient

from uikit import TEST_PASSWORD, sign_in
from tiui.accounts import AccountStore
from tiui.main import create_app
from tiui.security import SessionStore, csv_safe
from tiui.settings import Settings


def login(c, code="", staff_id="A1", role="agent"):
    return c.post("/api/session/login", json={"role": role, "staff_id": staff_id,
                                              "password": TEST_PASSWORD, "verification_code": code})


@pytest.fixture
def mfa_client(ui_settings):
    with TestClient(create_app(replace(ui_settings, require_mfa=True))) as c:
        secret = pyotp.random_base32()
        codes = c.app.state.accounts.enroll_mfa("A1", secret)
        yield c, secret, codes


def test_mfa_is_required_by_default_and_cannot_be_disabled_outside_demo():
    assert Settings().require_mfa
    with pytest.raises(ValueError):
        Settings(require_mfa=False)
    with pytest.raises(ValueError):
        Settings(allowed_hosts=("*",))


def test_mfa_enforcement_and_replay(mfa_client):
    c, secret, _ = mfa_client
    assert login(c).status_code == 401
    assert c.get("/api/session/me").status_code == 401
    assert not c.cookies.get("ti_session")
    otp = pyotp.TOTP(secret).now()
    assert login(c, otp).status_code == 200
    assert c.get("/api/session/me").json()["mfa"] is True
    assert login(c, otp).status_code == 401  # one-time counter cannot be reused


def test_password_only_cannot_access_an_account_without_mfa_provisioning(make_client):
    c = make_client(require_mfa=True)
    assert login(c).status_code == 401
    assert c.get("/api/team/agents").status_code == 401


def test_recovery_codes_are_single_use(mfa_client):
    c, _, codes = mfa_client
    assert login(c, codes[0]).status_code == 200
    assert login(c, codes[0]).status_code == 401
    assert login(c, codes[1]).status_code == 200


def test_failed_second_factor_locks_even_with_correct_password(mfa_client):
    c, _, _ = mfa_client
    c.app.state.limiter.limits["login"] = []
    for _ in range(5):
        assert login(c, "bad-code").status_code == 401
    assert login(c).status_code == 429


def test_wrong_role_is_counted_as_an_authentication_failure(client):
    client.app.state.limiter.limits["login"] = []
    for _ in range(5):
        assert login(client, role="team_lead").status_code == 401
    assert login(client).status_code == 429


def test_mfa_secret_encrypted_and_recovery_codes_hashed(mfa_client):
    c, secret, codes = mfa_client
    store = c.app.state.accounts
    text = store.path.read_text()
    assert secret not in text and all(code not in text for code in codes)
    assert json.loads(text)["A1"]["mfa_secret"].startswith("gAAAA")
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.with_suffix(".mfa.key").stat().st_mode) == 0o600


def test_mfa_codes_are_not_revealed_in_profile_or_team_response(mfa_client):
    c, secret, codes = mfa_client
    login(c, codes[0])
    text = c.get("/api/session/me").text + c.get("/api/session/security").text
    assert secret not in text and codes[0] not in text and 'recovery_hashes' not in text


def test_expired_authenticator_code_rejected(mfa_client):
    c, secret, _ = mfa_client
    store = c.app.state.accounts
    store.wall_clock = lambda: 2_000_000_000
    assert login(c, pyotp.TOTP(secret).at(2_000_000_000 - 120)).status_code == 401


def test_password_reset_preserves_second_factor_and_invalidates_cookie(mfa_client):
    c, secret, codes = mfa_client
    login(c, codes[0])
    c.app.state.accounts.set_password("A1", TEST_PASSWORD + "-new")  # also covers server CLI resets
    assert c.get("/api/session/me").status_code == 401
    assert c.app.state.accounts.has_mfa("A1")
    assert c.app.state.accounts.verify_mfa("A1", pyotp.TOTP(secret).now())


def test_account_removed_outside_server_invalidates_cookie(client):
    sign_in(client, "A1")
    AccountStore(client.app.state.settings.accounts_file).remove("A1")
    assert client.get("/api/session/me").status_code == 401


def test_password_change_requires_fresh_authentication_and_rotates_session(client):
    csrf = sign_in(client, "L1", reauthenticate=False)
    old_sid = client.cookies.get("ti_session")
    body = {"password": TEST_PASSWORD + "-new"}
    r = client.post("/api/team/agents/A1/password", json=body, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403 and r.json()["code"] == "reauthentication_required"
    assert client.app.state.accounts.verify("A1", TEST_PASSWORD)
    r = client.post("/api/session/reauthenticate", json={"password": TEST_PASSWORD}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert client.cookies.get("ti_session") != old_sid
    assert client.app.state.sessions.get(old_sid) is None
    assert client.post("/api/team/agents/A1/password", json=body, headers={"X-CSRF-Token": csrf}).status_code == 403
    assert client.post("/api/team/agents/A1/password", json=body, headers={"X-CSRF-Token": r.json()["csrf_token"]}).status_code == 200


def test_fresh_authentication_expires_after_five_minutes(client):
    now = [0.0]
    client.app.state.sessions.clock = lambda: now[0]
    csrf = sign_in(client, "L1")
    now[0] = 301
    r = client.post("/api/team/agents/A1/revoke", json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403 and r.json()["code"] == "reauthentication_required"


def test_reauthentication_requires_both_factors(mfa_client):
    c, _, codes = mfa_client
    login(c, codes[0])
    csrf = c.get("/api/session/me").json()["csrf_token"]
    assert c.post("/api/session/reauthenticate", json={"password": TEST_PASSWORD}, headers={"X-CSRF-Token": csrf}).status_code == 403
    r = c.post("/api/session/reauthenticate", json={"password": TEST_PASSWORD, "verification_code": codes[1]}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200


def test_reauthentication_does_not_extend_absolute_lifetime():
    now = [0.0]
    store = SessionStore(100, 200, clock=lambda: now[0])
    s = store.create("A1", "agent")
    now[0] = 80
    renewed = store.reauthenticate(s.sid)
    assert renewed.created == 0
    now[0] = 160
    assert store.get(renewed.sid)
    now[0] = 200
    assert store.get(renewed.sid) is None


def test_security_polling_does_not_keep_idle_session_alive(client):
    now = [0.0]
    client.app.state.sessions.clock = lambda: now[0]
    sign_in(client, "A1")
    now[0] = 899
    assert client.get("/api/session/state").json()["idle_seconds_left"] == 1
    assert client.get("/api/session/security").status_code == 200
    now[0] = 900
    assert client.get("/api/session/state").status_code == 401


def test_keepalive_requires_csrf_and_cannot_resurrect_expired_session(client):
    now = [0.0]
    client.app.state.sessions.clock = lambda: now[0]
    csrf = sign_in(client, "A1")
    now[0] = 100
    assert client.post("/api/session/keepalive", json={}).status_code == 403
    r = client.post("/api/session/keepalive", json={}, headers={"X-CSRF-Token": csrf})
    assert r.json()["idle_seconds_left"] == 900
    now[0] = 1000
    assert client.post("/api/session/keepalive", json={}, headers={"X-CSRF-Token": csrf}).status_code == 401


def test_session_limit_and_expiry_boundary():
    now = [0.0]
    store = SessionStore(60, 600, clock=lambda: now[0])
    sessions = []
    for _ in range(4):
        sessions.append(store.create("A1", "agent"))
        now[0] += 1
    assert store.get(sessions[0].sid) is None
    assert len(store.for_user("A1", sessions[-1].sid)) == 3
    now[0] = 63
    assert store.for_user("A1", sessions[-1].sid) == []


def test_sign_out_other_browsers_and_never_disclose_usable_cookies(client):
    csrf = sign_in(client, "A1")
    sid = client.cookies.get("ti_session")
    other = TestClient(client.app)
    sign_in(other, "A1")
    stranger = TestClient(client.app)
    sign_in(stranger, "A2")
    info = client.get("/api/session/security")
    assert sid not in info.text and other.cookies.get("ti_session") not in info.text
    assert len(info.json()["sessions"]) == 2
    r = client.post("/api/session/logout-others", json={}, headers={"X-CSRF-Token": csrf})
    assert r.json()["ended"] == 1
    assert client.get("/api/session/me").status_code == 200
    assert other.get("/api/session/me").status_code == 401
    assert stranger.get("/api/session/me").status_code == 200


def test_security_events_scoped_to_owner(client):
    client.app.state.access_log.record(action="sign_in", user_id="A2", object_id="other-user-secret")
    sign_in(client, "A1")
    text = client.get("/api/session/security").text
    assert "other-user-secret" not in text and '"A2"' not in text


def test_host_spoof_and_cross_site_fetch_refused(client):
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400
    assert login(client).status_code == 200
    r = client.get("/api/status", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = client.post("/api/session/login", json={"role": "agent", "staff_id": "A1", "password": TEST_PASSWORD}, headers={"Origin": "https://attacker.example"})
    assert r.status_code == 403


def test_non_json_and_misreported_body_size_refused(client):
    assert client.post("/api/session/login", content="x", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/session/login", content="x" * 40000, headers={"Content-Type": "application/json", "Content-Length": "1"}).status_code == 413
    assert client.post("/api/session/login", content="{}", headers={"Content-Type": "application/json", "Content-Length": "1"}).status_code == 400


def test_non_ascii_csrf_refused_without_server_error(client):
    sign_in(client, "A1")
    r = client.post("/api/session/logout", json={}, headers={b"X-CSRF-Token": b"\xff"})
    assert r.status_code == 403


def test_raw_attacker_paths_are_not_logged(client):
    client.get("/api/private-customer-name", headers={"Sec-Fetch-Site": "cross-site"})
    rows = client.app.state.access_log.con().execute("SELECT action FROM ui.access_log").fetchall()
    assert not any("private-customer-name" in r[0] for r in rows)


def test_common_passwords_and_whitespace_csv_formula():
    from tiui.accounts import check_password_rules
    assert check_password_rules("Password123456!") is not None
    assert csv_safe("  =HYPERLINK(1)").startswith("'")
    assert csv_safe("\n=SUM(1)").startswith("'")
