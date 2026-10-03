"""Temporary shared access uses DuckDB staff eligibility without persisting credentials."""
from dataclasses import replace

import duckdb
import pytest
from fastapi.testclient import TestClient

from tiui.main import create_app
from tiui.settings import Settings

PASSWORD = 'Demo-test-only-123!'


def test_shared_access_requires_local_demo_without_mfa():
    with pytest.raises(ValueError):
        Settings(demo_password=PASSWORD)
    with pytest.raises(ValueError):
        Settings(demo=True, demo_password=PASSWORD)
    with pytest.raises(ValueError):
        Settings(demo=True, require_mfa=False, demo_password=PASSWORD, allowed_hosts=('example.com',))
    assert PASSWORD not in repr(Settings(demo=True, require_mfa=False, demo_password=PASSWORD))


def test_shared_access_uses_live_staff_and_preserves_accounts(ui_settings, tmp_path):
    before = ui_settings.accounts_file.read_bytes()
    accounts = tmp_path / 'no-accounts-created.json'
    settings = replace(ui_settings, accounts_file=accounts, demo_password=PASSWORD)
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.get('/api/session/options').json()['shared_access'] is True
        for sid, role in [('A1', 'agent'), ('A2', 'agent'), ('L1', 'team_lead'), ('L2', 'team_lead')]:
            r = c.post('/api/session/login', json={'staff_id': sid, 'role': role, 'password': PASSWORD})
            assert r.status_code == 200
            assert c.get('/api/session/me').json()['agent_id'] == sid
        # Unknown IDs, inactive staff, forbidden roles, wrong tab and wrong password still fail.
        for sid, role, password in [('MISSING', 'agent', PASSWORD), ('X1', 'agent', PASSWORD),
                                    ('Q1', 'agent', PASSWORD), ('L1', 'agent', PASSWORD), ('A1', 'agent', 'wrong')]:
            app.state.limiter._hits.clear()
            assert c.post('/api/session/login', json={'staff_id': sid, 'role': role, 'password': password}).status_code == 401
        app.state.limiter._hits.clear()
        assert c.post('/api/session/login', json={'staff_id': 'L1', 'role': 'team_lead', 'password': PASSWORD}).status_code == 200
        me = c.get('/api/session/me').json()
        agents = c.get('/api/team/agents').json()['agents']
        assert agents and all(a['has_account'] and a['set_at'] is None for a in agents)
        headers = {'X-CSRF-Token': me['csrf_token']}
        assert c.post('/api/team/agents/A1/password', json={'password': PASSWORD}, headers=headers).status_code == 409
        assert c.post('/api/team/agents/A1/revoke', json={}, headers=headers).status_code == 409
        with duckdb.connect(str(settings.warehouse)) as con:
            con.execute("UPDATE curated.agents SET status='terminated' WHERE agent_id='L1'")
        assert c.get('/api/session/me').status_code == 401
    assert not accounts.exists()
    assert not accounts.with_suffix('.mfa.key').exists()
    assert ui_settings.accounts_file.read_bytes() == before
