from datetime import date, datetime

import duckdb
import pytest

from trustissues.ask.data.execute import execute
from trustissues.ask.data.metric_templates import METRICS, render_metric
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


@pytest.mark.parametrize('metric', sorted(METRICS))
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
