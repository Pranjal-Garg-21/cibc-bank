from datetime import date, datetime

import duckdb
import pytest

from trustissues.ask.data.execute import execute
from trustissues.ask.data.metric_templates import render_metric
from trustissues.ask.data.schema_graph import SEMANTIC_JOIN_KEYS
from trustissues.ask.data.semantic import SCHEMAS, create_semantic_fixtures
from trustissues.ask.data.validator import QueryPolicy, validate


START, END = date(2026, 9, 1), date(2026, 10, 1)
OBSERVED = datetime(2026, 10, 1)


def insert(con, table, **row):
    con.execute(f"INSERT INTO gold.semantic_{table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    create_semantic_fixtures(c)
    for key, opened, dpd, cured, outcome, outcome_date, hardship in [
        ('A',date(2026,8,1),10,date(2026,9,5),None,None,True),
        ('B',date(2026,7,1),40,None,None,None,False),
        ('C',date(2026,9,1),None,None,None,None,True),
        ('D',date(2026,7,20),0,date(2026,8,15),'cured',date(2026,8,15),False),
    ]:
        insert(c,'cases',case_id=key,case_open_date=opened,current_dpd=dpd,cure_date=cured,outcome=outcome,
               outcome_date=outcome_date,hardship_flag=hardship,queue='early',primary_product='card',
               insolvency_hold_flag=False,deceased_hold_flag=False)
    for i, status, paid, status_time in [(1,'kept',100,datetime(2026,9,11)),(2,'partially_kept',50,datetime(2026,9,12)),
                                       (3,'broken',0,datetime(2026,9,13)),(4,'open',None,None),
                                       (5,'kept',100,datetime(2026,10,3))]:
        insert(c,'promises',ptp_id=f'P{i}',case_id='A',product='card',agent_site='self_serve',
               ptp_created_ts=datetime(2026,9,1),ptp_due_date=date(2026,9,10),ptp_status=status,
               amount_paid_against=paid,paid_date=date(2026,9,10) if i<3 else None,status_ts=status_time,
               prior_broken_ptp_count=i-1,assignment_method='random',test_cell='champion')
    for i,key,channel,direction,rpc,ptp,cost in [(1,'A','call','outbound',True,True,1),
                                             (2,'A','call','outbound',False,False,3),
                                             (3,'B','sms','inbound',True,True,2),
                                             (4,'B','system','internal',False,False,0)]:
        insert(c,'contacts',contact_id=f'H{i}',case_id=key,channel=channel,direction=direction,rpc_flag=rpc,
               ptp_made_flag=ptp,contact_cost_cad=cost,contact_ts_local=datetime(2026,9,10),contact_hour_local=10)
    for i, dpd,status in [(1,5,'active'),(2,0,'blocked'),(3,None,'active')]:
        insert(c,'accounts',account_id=f'ACC{i}',snapshot_date=date(2026,9,28),product='card',product_code='test',
               account_status=status,dpd=dpd,chargeoff_date=date(2026,9,15) if i==1 else None,
               chargeoff_amount=99 if i==1 else None)
        insert(c,'snapshots',account_id=f'ACC{i}',snapshot_date=date(2026,3,31),product='card',dpd=15 if i!=3 else None,
               account_status='active')
        insert(c,'snapshots',account_id=f'ACC{i}',snapshot_date=date(2026,6,30),product='card',dpd=45 if i==1 else 20,
               account_status='active')
        insert(c,'payroll',golden_customer_id=f'G{i}',snapshot_date=date(2026,9,28),payroll_deposit_flag=i!=3,
               payroll_delay_days=8 if i==1 else 3)
    yield c
    c.close()


def parameters(metric):
    if metric in {'promise_kept_rate','broken_promise_rate'}:
        return dict(start=START,end=END,observation_end=OBSERVED,random_only=False)
    if metric=='collected_amount':
        return dict(start=START,end=END,observation_end=OBSERVED)
    if metric=='average_dpd':return {}
    if metric=='hardship_rate':return dict(start=date(2026,7,1),end=END)
    if metric in {'delinquency_rate','payroll_delay_share','write_off_rate'}:
        p=dict(snapshot_date=date(2026,9,28))
        if metric=='delinquency_rate':p['product']='card'
        if metric=='write_off_rate':p.update(start=START,end=END)
        return p
    if metric=='roll_rate':
        return dict(start_snapshot=date(2026,3,31),end_snapshot=date(2026,6,30),start_dpd_min=1,start_dpd_max=30,exclude_holds=True)
    if metric=='cure_rate':return dict(start=START,end=END,observation_end=OBSERVED)
    return dict(start=START,end=END)


EXPECTED = {'promise_kept_rate':100/3,'broken_promise_rate':100/3,'collected_amount':150,
            'rpc_rate':50,'ptp_rate':100,'cost_per_contact':2,'cost_per_rpc':3,'hardship_rate':50,
            'average_dpd':25,'delinquency_rate':50,'payroll_delay_share':50,'write_off_rate':100/3,
            'roll_rate':50,'contact_attempts_per_case':2/3,'cure_rate':50}


@pytest.mark.parametrize('metric', sorted(EXPECTED))
def test_all_15_templates_validate_and_compute(metric,con):
    q=render_metric(metric,parameters=parameters(metric))
    checked=validate(q.sql,QueryPolicy('analyst',SCHEMAS,join_keys=SEMANTIC_JOIN_KEYS))
    result=execute(checked,q.parameters,con=con)
    assert result.rows[0][0] == pytest.approx(EXPECTED[metric])
    if metric=='write_off_rate':assert result.rows[0][1:3] == (1,99)


def test_cap_at_grouping_future_outcome_and_empty_denominator(con):
    q=render_metric('broken_promise_rate',parameters=parameters('broken_promise_rate'),
                    dimensions=('prior_broken_ptp_count',),cap_at=3)
    rows=execute(validate(q.sql,QueryPolicy('analyst',SCHEMAS)),q.parameters,con=con).rows
    assert len(rows)==3  # open and post-cutoff kept promise excluded
    con.execute('DELETE FROM gold.semantic_promises')
    q=render_metric('promise_kept_rate',parameters=parameters('promise_kept_rate'))
    assert execute(validate(q.sql,QueryPolicy('analyst',SCHEMAS)),q.parameters,con=con).rows[0][0] is None


def test_cohort_cure_is_distinct_from_period_cure(con):
    q=render_metric('cure_rate',variant='cohort_30d',parameters=dict(start=date(2026,7,1),end=date(2026,8,1),observation_end=OBSERVED))
    assert execute(validate(q.sql,QueryPolicy('analyst',SCHEMAS)),q.parameters,con=con).rows[0] == (50.,1,2)


def test_identifier_and_variant_injection_rejected():
    with pytest.raises(ValueError):render_metric('average_dpd',parameters={},dimensions=('queue; DROP TABLE gold.t',))
    with pytest.raises(ValueError):render_metric('average_dpd',parameters={},variant='guess')
    with pytest.raises(ValueError):render_metric('rpc_rate',parameters={'start': START})


def test_write_off_includes_older_snapshots_and_full_as_of_denominator(con):
    con.execute('DELETE FROM gold.semantic_accounts')
    for key, snapshot, chargeoff, amount in [
        ('current', date(2026,9,28), None, None),
        ('closed', date(2024,11,1), None, None),
        ('first', date(2025,9,29), date(2025,9,29), 100),
        ('last', date(2026,9,28), date(2026,9,28), 50),
        ('before', date(2025,9,28), date(2025,9,28), 200),
        ('future', date(2026,9,29), date(2026,9,29), 400),
    ]:
        insert(con,'accounts',account_id=key,product='card',snapshot_date=snapshot,
               chargeoff_date=chargeoff,chargeoff_amount=amount)
    q=render_metric('write_off_rate',parameters=dict(start=date(2025,9,29),end=date(2026,9,29),
                    snapshot_date=date(2026,9,28)),dimensions=('product',))
    result=execute(validate(q.sql,QueryPolicy('team_lead',SCHEMAS)),q.parameters,con=con)
    # Old closed and pre-window charged-off accounts also belong in the population.
    assert result.rows == (('card',40.0,2,150.0,5),)
    q=render_metric('write_off_rate',parameters=dict(start=date(2025,9,29),end=date(2026,9,28),
                    snapshot_date=date(2026,9,28)))
    result=execute(validate(q.sql,QueryPolicy('team_lead',SCHEMAS)),q.parameters,con=con)
    assert result.rows == ((20.0,1,100.0,5),)  # exclusive charge-off end, same population


def test_delinquency_excludes_noncurrent_closed_and_charged_off_accounts(con):
    for key, snapshot, status in [
        ('old_active',date(2026,9,27),'active'),
        ('future_active',date(2026,9,29),'active'),
        ('closed',date(2026,9,28),'closed'),
        ('charged',date(2026,9,28),'charged_off'),
        ('unknown',date(2026,9,28),'unknown'),
    ]:
        insert(con,'accounts',account_id=key,product='card',snapshot_date=snapshot,
               account_status=status,dpd=100)
    q=render_metric('delinquency_rate',parameters=parameters('delinquency_rate'))
    result=execute(validate(q.sql,QueryPolicy('team_lead',SCHEMAS)),q.parameters,con=con)
    # Existing fixture retains current active + blocked observed DPD, excludes NULL DPD.
    assert result.rows == ((50.0,1,2),)


def test_out_of_hours_supplied_flag_inner_join_and_filters(con):
    con.execute("DELETE FROM gold.semantic_contacts")
    con.execute("UPDATE gold.semantic_cases SET province='ON' WHERE case_id='A'")
    for i, case, channel, direction, flag, timestamp in [
        (1,'A','call','outbound',False,datetime(2026,9,1)),
        (2,'A','call','outbound',True,datetime(2026,9,2)),
        (3,'A','call','outbound',None,datetime(2026,9,3)),
        (4,'B','call','outbound',False,datetime(2026,9,3)),
        (5,'missing','call','outbound',False,datetime(2026,9,3)),
        (6,'A','sms','outbound',False,datetime(2026,9,3)),
        (7,'A','call','inbound',False,datetime(2026,9,3)),
        (8,'A','call','outbound',False,datetime(2026,8,31)),
    ]:
        insert(con,'contacts',contact_id=str(i),case_id=case,channel=channel,direction=direction,
               within_permitted_hours_flag=flag,contact_ts_local=timestamp)
    q=render_metric('out_of_hours_share',parameters={'start':START},dimensions=('province',))
    checked=validate(q.sql,QueryPolicy('team_lead',SCHEMAS,join_keys=SEMANTIC_JOIN_KEYS))
    result=execute(checked,q.parameters,con=con)
    assert result.columns == ('province','out_of_hours_pct','calls')
    assert result.rows == (('ON',33.33,3),)


def test_dispute_periods_boundaries_and_compact_labels(con):
    con.execute('DELETE FROM gold.semantic_contacts')
    for i, day, flag in [(1,6,True),(2,7,True),(3,14,True),(4,15,True),(5,22,True),(6,23,True),(7,16,False)]:
        insert(con,'contacts',contact_id=str(i),contact_ts_local=datetime(2026,9,day),dispute_raised_flag=flag)
    params=dict(start=date(2026,9,15),end=date(2026,9,23),comparison_start=date(2026,9,7),comparison_end=date(2026,9,15))
    q=render_metric('dispute_count',parameters=params)
    result=execute(validate(q.sql,QueryPolicy('team_lead',SCHEMAS)),q.parameters,con=con)
    assert result.columns == ('period','disputes')
    assert result.rows == (('15-22 Sep',2),('7-14 Sep',2))
    assert 'period_label' not in params
    with pytest.raises(ValueError,match='overlap'):
        render_metric('dispute_count',parameters={**params,'comparison_end':date(2026,9,16)})
    with pytest.raises(ValueError,match='requires grouping'):
        render_metric('out_of_hours_share',parameters={'start':START})
    with pytest.raises(ValueError,match='ISO'):
        render_metric('out_of_hours_share',parameters={'start':'bad date'},dimensions=('province',))


def test_certification_pilot_counts_randomised_hardship_assignments_only(con):
    """BQ-014: unit = one randomised_pilot assignment; certification and hardship as recorded at assignment."""
    for aid, method, cert, hardship, assigned, cured, hold in [
        ('A1', 'randomised_pilot', True, True, datetime(2026, 7, 1), date(2026, 7, 20), False),   # cured in 30 days
        ('A2', 'randomised_pilot', True, True, datetime(2026, 7, 1), date(2026, 8, 15), False),   # cured too late
        ('A3', 'randomised_pilot', False, True, datetime(2026, 7, 1), date(2026, 6, 1), False),   # cured before assignment
        ('A4', 'randomised_pilot', False, True, datetime(2026, 7, 1), None, False),
        ('A5', 'rule_router', True, True, datetime(2026, 7, 1), date(2026, 7, 5), False),         # not randomised
        ('A6', 'randomised_pilot', True, False, datetime(2026, 7, 1), date(2026, 7, 5), False),   # not hardship
        ('A7', 'randomised_pilot', False, True, datetime(2026, 9, 20), date(2026, 9, 21), False), # under 30 days old
        ('A8', 'randomised_pilot', False, True, datetime(2026, 7, 1), date(2026, 7, 5), True),    # insolvency hold
    ]:
        insert(con, 'assignments', assignment_id=aid, case_id='C-' + aid, assignment_method=method,
               certified_for_vulnerable_flag=cert, hardship_flag_at_assignment=hardship, assigned_ts=assigned,
               cure_date=cured, insolvency_hold_flag=hold)
    def run(hardship_only):
        q = render_metric('cure_rate', variant='certification_pilot',
                          parameters=dict(hardship_only=hardship_only, observation_end='2026-09-29'))
        return sorted(execute(validate(q.sql, QueryPolicy('analyst', SCHEMAS)), q.parameters, con=con).rows)
    assert run(True) == [('certified', 50., 1, 2), ('not_certified', 0., 0, 2)]
    assert run(False) == [('certified', 100 * 2 / 3, 2, 3), ('not_certified', 0., 0, 2)]
    with pytest.raises(ValueError):  # grouped by certification only
        render_metric('cure_rate', variant='certification_pilot', dimensions=('queue',),
                      parameters=dict(hardship_only=True, observation_end='2026-09-29'))
    with pytest.raises(ValueError):
        render_metric('cure_rate', variant='certification_pilot', parameters=dict(hardship_only='yes', observation_end='2026-09-29'))
