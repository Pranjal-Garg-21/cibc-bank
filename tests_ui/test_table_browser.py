"""The demo table browser reads actual rows, masks sensitive data and refuses arbitrary SQL/access."""
from dataclasses import replace

import duckdb

from conftest import sign_in


def test_table_browser_requires_team_lead_demo(client):
    assert client.get('/api/tables').status_code == 401
    assert client.get('/api/tables/curated/agents').status_code == 401
    sign_in(client, 'A1')
    assert client.get('/api/tables').status_code == 403
    assert client.get('/api/tables/curated/agents').status_code == 403
    sign_in(client, 'L1')
    client.app.state.settings = replace(client.app.state.settings, demo=False, require_mfa=True)
    assert client.get('/api/tables').status_code == 403


def test_catalog_and_paginated_rows_mask_staff_names(client):
    sign_in(client, 'L1')
    catalog = client.get('/api/tables').json()['tables']
    assert {'schema': 'curated', 'name': 'agents', 'rows': 7} in catalog
    r = client.get('/api/tables/curated/agents?limit=2')
    assert r.status_code == 200
    body = r.json()
    assert body['total'] == 7 and len(body['data']['rows']) == 2
    cols = body['data']['columns']
    assert body['data']['rows'][0][cols.index('agent_id')] == 'A1'
    assert body['data']['rows'][0][cols.index('agent_display_name')] == '***'
    assert 'Agent One' not in r.text
    second = client.get('/api/tables/curated/agents?limit=2&offset=2').json()
    assert second['data']['rows'][0][cols.index('agent_id')] == 'L1'


def test_empty_stub_keeps_column_structure(client, ui_settings):
    with duckdb.connect(str(ui_settings.warehouse)) as con:
        con.execute('CREATE TABLE gold.nba_decisions (recommendation_id VARCHAR, risk_score DOUBLE)')
    sign_in(client, 'L1')
    body = client.get('/api/tables/gold/nba_decisions').json()
    assert body['total'] == 0 and body['data']['rows'] == []
    assert body['data']['columns'] == ['recommendation_id', 'risk_score']
    assert [c['type'] for c in body['columns']] == ['VARCHAR', 'DOUBLE']


def test_hidden_tables_and_unclassified_values(client, ui_settings):
    with duckdb.connect(str(ui_settings.warehouse)) as con:
        con.execute('CREATE TABLE gold.audit_log (private_event VARCHAR)')
        con.execute('CREATE TABLE curated._private (secret VARCHAR)')
        con.execute("CREATE TABLE gold.sample_data AS SELECT 'PRIVATE CUSTOMER TEXT' AS unknown_text, 'G1' AS golden_customer_id")
    sign_in(client, 'L1')
    catalog = client.get('/api/tables').json()['tables']
    assert not any(t['name'] in ('audit_log', '_private') for t in catalog)
    assert client.get('/api/tables/gold/audit_log').status_code == 404
    assert client.get('/api/tables/curated/_private').status_code == 404
    r = client.get('/api/tables/gold/sample_data')
    assert 'PRIVATE CUSTOMER TEXT' not in r.text
    assert r.json()['data']['rows'] == [['***', 'G1']]


def test_table_browser_rejects_sql_paths_and_unbounded_pages(client):
    sign_in(client, 'L1')
    for path in ('raw/agents', 'information_schema/tables', 'curated/missing', "curated/agents%22%3BDROP%20TABLE%20agents"):
        assert client.get('/api/tables/' + path).status_code == 404
    for query in ('limit=101', 'limit=0', 'offset=-1', 'offset=1000001'):
        assert client.get('/api/tables/curated/agents?' + query).status_code == 422
    assert client.get('/api/tables/curated/agents').json()['total'] == 7
