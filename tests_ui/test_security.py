"""Security behaviour of the UI API (docs/ui_design.md §9)."""
from __future__ import annotations

import duckdb

from conftest import PAIR_1, sign_in

from tiui.security import RateLimiter, SessionStore, csv_safe, mask_phone, mask_rows

SECURITY_HEADERS = ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy",
                    "Permissions-Policy", "Cross-Origin-Opener-Policy")


def test_everything_needs_a_session(client):
    for path in ("/api/session/me", "/api/identity/queue", f"/api/identity/pairs/{PAIR_1}", "/api/status",
                 "/api/status/reports/data_quality_report.md"):
        r = client.get(path)
        assert r.status_code == 401, path
        assert set(r.json()) == {"code", "message", "request_id"}


def test_every_route_is_guarded(client):
    """Deny by default: every API route except the listed public ones refuses an anonymous caller."""
    public = {"/api/health", "/api/session/options", "/api/session/staff", "/api/session/login"}
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in public or path.startswith("/api/docs"):
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            url = path.replace("{rec_id}", "R1").replace("{pair_id}", PAIR_1).replace("{name}", "x.md")
            r = client.request(method, url, json={} if method == "POST" else None)
            assert r.status_code in (401, 501), (method, path, r.status_code)


def test_demo_login_off_by_default(make_client):
    c = make_client(demo_login=False)
    assert c.post("/api/session/login", json={"agent_id": "A1"}).status_code == 404
    assert c.get("/api/session/staff").status_code == 404


def test_inactive_or_unknown_staff_cannot_sign_in(client):
    assert client.post("/api/session/login", json={"agent_id": "X1"}).status_code == 401
    assert client.post("/api/session/login", json={"agent_id": "NOPE"}).status_code == 401
    assert client.post("/api/session/login", json={"agent_id": "A1' OR 1=1 --"}).status_code == 422


def test_session_cookie_flags(client):
    r = client.post("/api/session/login", json={"agent_id": "A1"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie


def test_secure_cookie_and_hsts_over_https(make_client):
    c = make_client(https=True)
    r = c.post("/api/session/login", json={"agent_id": "A1"})
    assert "secure" in r.headers["set-cookie"].lower()
    assert r.headers["strict-transport-security"].startswith("max-age=")


def test_new_session_id_on_each_sign_in(client):
    client.post("/api/session/login", json={"agent_id": "A1"})
    first = client.cookies.get("ti_session")
    client.post("/api/session/login", json={"agent_id": "A1"})
    assert client.cookies.get("ti_session") != first
    assert client.app.state.sessions.get(first) is None


def test_headers_on_every_response(client):
    for r in (client.get("/api/health"), client.get("/api/status"), client.get("/api/nope")):
        for h in SECURITY_HEADERS:
            assert h in r.headers, (r.url, h)
        assert r.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert "unsafe-inline" not in r.headers["content-security-policy"]


def test_writes_need_csrf_token(client):
    csrf = sign_in(client, "Q1")
    body = {"pair_id": PAIR_1, "decision": "match"}
    assert client.post("/api/identity/decisions", json=body).status_code == 403
    assert client.post("/api/identity/decisions", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/identity/decisions", json=body, headers={"X-CSRF-Token": csrf}).status_code == 200


def test_foreign_origin_refused(client):
    csrf = sign_in(client, "Q1")
    r = client.post("/api/identity/decisions", json={"pair_id": PAIR_1, "decision": "match"},
                    headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "bad_origin"


def test_oversized_body_refused(client):
    csrf = sign_in(client, "Q1")
    r = client.post("/api/identity/decisions", content=b"x" * 40000,
                    headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_logout_ends_session(client):
    csrf = sign_in(client, "A1")
    assert client.post("/api/session/logout", json={}, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/session/me").status_code == 401


def test_deactivated_user_loses_access_at_once(client, ui_settings):
    sign_in(client, "A1")
    con = duckdb.connect(str(ui_settings.warehouse))
    con.execute("UPDATE curated.agents SET status = 'terminated' WHERE agent_id = 'A1'")
    con.close()
    assert client.get("/api/session/me").status_code == 401


def test_errors_do_not_leak_internals(client):
    sign_in(client, "Q1")
    r = client.get("/api/identity/pairs/not-a-pair")
    assert r.status_code == 404
    assert "Traceback" not in r.text and "SELECT" not in r.text


def test_login_rate_limited(client):
    codes = [client.post("/api/session/login", json={"agent_id": "NOPE"}).status_code for _ in range(7)]
    assert codes[-1] == 429


def test_refusals_are_logged(client, ui_settings):
    client.get("/api/status")
    client.app.state.access_log.close()
    con = duckdb.connect(str(ui_settings.ui_audit))
    assert con.execute("SELECT count(*) FROM ui.access_log WHERE outcome = '401'").fetchone()[0] >= 1
    con.close()


def test_no_connection_left_open(client, ui_settings):
    """After a request the warehouse can be opened for writing: the UI never holds the lock (§3.1)."""
    sign_in(client, "Q1")
    client.get("/api/identity/queue")
    con = duckdb.connect(str(ui_settings.warehouse))  # would fail if a connection were still open
    con.close()


# --- unit tests for the helpers ----------------------------------------------------------------------------

def test_session_expiry():
    now = [0.0]
    store = SessionStore(idle_seconds=60, max_seconds=600, clock=lambda: now[0])
    s = store.create("A1", "agent")
    now[0] = 59
    assert store.get(s.sid) is not None
    now[0] = 59 + 61
    assert store.get(s.sid) is None
    s = store.create("A1", "agent")
    for t in range(0, 700, 50):
        now[0] = 120 + t
        store.get(s.sid)
    assert store.get(s.sid) is None  # absolute limit even when active


def test_rate_limiter_window():
    now = [0.0]
    rl = RateLimiter(clock=lambda: now[0])
    assert all(rl.hit("login", "ip") == 0 for _ in range(5))
    assert rl.hit("login", "ip") > 0
    now[0] = 61
    assert rl.hit("login", "ip") == 0


def test_csv_formula_escaping():
    assert csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert csv_safe("+1") == "'+1" and csv_safe("@x") == "'@x" and csv_safe("-2") == "'-2"
    assert csv_safe("fine\nnote") == "fine note"


def test_phone_masking():
    assert mask_phone("+14165550123") == "•••• 0123"
    assert mask_phone(None) is None


def test_fallback_masks_sensitive_columns():
    cols, rows = mask_rows(["first_name", "dpd"], [["ANNA", 30]], "agent")
    assert rows[0][cols.index("dpd")] == 30
    if "first_name" in cols:
        assert rows[0][cols.index("first_name")] != "ANNA"


def test_app_page_gets_a_fresh_style_nonce(make_client, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<meta name="csp-nonce" content="__CSP_NONCE__">', encoding="utf-8")
    c = make_client(dist_dir=dist)
    r1, r2 = c.get("/"), c.get("/queue")
    n1 = r1.text.split('content="')[1].split('"')[0]
    assert n1 != "__CSP_NONCE__" and f"'nonce-{n1}'" in r1.headers["content-security-policy"]
    assert n1 not in r2.text  # a new nonce on every response
    assert "unsafe-inline" not in r1.headers["content-security-policy"]
    assert "script-src 'self';" in r1.headers["content-security-policy"]  # scripts never get a nonce
