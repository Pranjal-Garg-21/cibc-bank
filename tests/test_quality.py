from datetime import date, datetime, timedelta

import duckdb

from trustissues.common.fixtures import load_fixtures
from trustissues.factory.quality.anomaly import Observation, OpsEvent, detect, load_history, record_observation
from trustissues.factory.quality.contract_checks import publish_allowed, run_checks
from trustissues.factory.quality.check_report import render_report


NOW = datetime(2026,9,28,12)


def checks(con, **kwargs):
    allowed = {r[0] for r in con.execute('DESCRIBE gold.decisioning_customer').fetchall()}
    sources = [(r[0],r[1]) for r in con.execute('SELECT source_system,source_key FROM gold.customer_xref').fetchall()]
    return run_checks(con,as_of_date=date(2026,9,28),checked_at=NOW,source_records=sources,
                      source_safety={'G-000002': {'cease_contact': True}},decisioning_allowlist=allowed,**kwargs)


def test_fixture_quality_and_missing_evidence():
    c=duckdb.connect(':memory:')
    load_fixtures(c)
    results=checks(c)
    assert len(results)==8
    assert all(r.status=='pass' for r in results)
    assert publish_allowed(results)
    missing=run_checks(c,as_of_date=date(2026,9,28),checked_at=NOW)
    assert not publish_allowed(missing)
    assert {r.rule_id for r in missing if r.status=='not_checked'} == {2,4,5}
    c.close()


def test_blocking_identity_safety_and_sensitive_fields():
    c=duckdb.connect(':memory:')
    load_fixtures(c)
    c.execute("UPDATE gold.customer_xref SET match_confidence=.5 WHERE status='linked'")
    c.execute("UPDATE gold.c360_customer SET cease_contact=false WHERE golden_customer_id='G-000002'")
    c.execute('ALTER TABLE gold.decisioning_customer ADD COLUMN full_name VARCHAR')
    results=checks(c)
    assert {r.rule_id for r in results if r.status=='fail'} == {3,4,5}
    assert not publish_allowed(results)
    c.close()


def test_warning_failures_do_not_block_and_latest_account_snapshot_used():
    c=duckdb.connect(':memory:')
    load_fixtures(c)
    c.execute("UPDATE gold.c360_customer SET date_of_birth=DATE '2010-01-01',refresh_ts=TIMESTAMP '2026-09-26',total_balance=total_balance+1")
    results=checks(c)
    assert {r.rule_id for r in results if r.status=='fail'} == {6,7,8}
    assert publish_allowed(results)
    report=render_report(results,generated_at=NOW,issues=['DOB needs review | source'])
    assert 'Publish gate: PASS' in report and 'warn' in report and '\\|' in report
    c.close()


def test_unmapped_source_is_blocking():
    c=duckdb.connect(':memory:')
    load_fixtures(c)
    results=run_checks(c,as_of_date=date(2026,9,28),checked_at=NOW,source_records=[('crm','missing')])
    assert next(r for r in results if r.rule_id==2).status=='fail'
    c.close()


def test_anomaly_history_and_incident_evidence():
    history=[Observation('contacts','row_count',100+i,NOW-timedelta(days=4-i)) for i in range(3)]
    current=Observation('contacts','row_count',500,NOW)
    assert detect(current,[]).status=='insufficient_history'
    assert detect(current,history).status=='alert'
    event=OpsEvent('INC1',('contacts',),NOW-timedelta(hours=1),NOW+timedelta(hours=1))
    found=detect(current,history,[event])
    assert found.status=='explained' and found.event_ids==('INC1',)
    assert detect(current,history,[OpsEvent('other',('accounts',),event.start_ts,event.end_ts)]).status=='alert'
    assert detect(Observation('contacts','row_count',101,NOW),history).status=='normal'
    # A changed constant baseline must alert rather than divide by zero.
    constant=[Observation('contacts','row_count',100,NOW-timedelta(days=i+1)) for i in range(3)]
    assert detect(current,constant).status=='alert'


def test_observation_history_is_separate_and_rerunnable():
    c=duckdb.connect(':memory:')
    old=Observation('contacts','row_count',100,NOW-timedelta(days=1))
    record_observation(c,old)
    record_observation(c,old)
    now=Observation('contacts','row_count',120,NOW)
    record_observation(c,now)
    assert load_history(c,now)==[old]
    c.close()
