"""Small populated fixture over the stub tables, so every owner can build and test before real gold lands.

load_fixtures(con) applies the stubs and inserts a few consistent, FAKE rows: 3 customers (G-000001..3), 4 promises,
safeguards (one customer on cease-contact, one in hardship), a timeline, one text feature, one decision and override.
Re-running replaces the fixture rows.

Safety: it empties the stub tables first, so it refuses to run on a real warehouse. It only runs on an in-memory
database or a file whose name contains 'fixture' (e.g. warehouse/fixtures.duckdb).
"""
from __future__ import annotations

from .interfaces import DecisionCard, Reason, BlockedAction, EvidenceQuote, apply_stubs

_TABLES = ["customer_xref", "c360_customer", "c360_account", "decisioning_customer", "promises_enriched",
           "customer_safeguards", "bucket_transitions", "customer_timeline", "text_features", "nba_decisions",
           "nba_overrides", "audit_log"]


def _is_fixture_db(con) -> bool:
    files = [row[2] for row in con.execute("PRAGMA database_list").fetchall() if row[0] != "system"]
    return all(not f or "fixture" in f.lower() for f in files)  # empty path = in-memory


def load_fixtures(con) -> None:
    if not _is_fixture_db(con):
        raise RuntimeError("load_fixtures empties the gold stub tables; use an in-memory DB or a file named *fixture*")
    apply_stubs(con)
    for t in _TABLES:
        con.execute(f"DELETE FROM gold.{t}")

    con.execute("""INSERT INTO gold.customer_xref VALUES
        ('G-000001','crm','CRM-1001','name_dob_phone',0.99,'linked'),
        ('G-000001','card','CARD-77','splink',0.97,'linked'),
        ('G-000002','crm','CRM-1002','national_id',1.0,'singleton'),
        ('G-000003','crm','CRM-1003','national_id',1.0,'singleton')""")

    con.execute("""INSERT INTO gold.c360_customer VALUES
        ('G-000001','{}','name_dob_phone',0.97,'TEST CUSTOMER ONE',DATE '1980-01-01','mass','ON','yes','yes','yes','yes',false,false,false,false,false,5200,1300,35,'31-60','broken','crm|card',TIMESTAMP '2026-09-28 06:00:00'),
        ('G-000002','{}','national_id',1.0,'TEST CUSTOMER TWO',DATE '1975-05-05','affluent','QC','yes','no','yes','yes',true,false,false,false,false,900,900,70,'61-90','open','crm',TIMESTAMP '2026-09-28 06:00:00'),
        ('G-000003','{}','national_id',1.0,'TEST CUSTOMER THREE',DATE '1990-09-09','mass','BC','yes','yes','yes','yes',false,false,false,false,true,12000,2400,15,'1-30','none','crm',TIMESTAMP '2026-09-28 06:00:00')""")

    con.execute("""INSERT INTO gold.c360_account VALUES
        ('ACC-1','G-000001','card',DATE '2026-09-28',3000,800,35,'31-60','open'),
        ('ACC-2','G-000001','loan',DATE '2026-09-28',2200,500,10,'1-30','open'),
        ('ACC-3','G-000002','loan',DATE '2026-09-28',900,900,70,'61-90','open'),
        ('ACC-4','G-000003','card',DATE '2026-09-28',12000,2400,15,'1-30','open')""")

    con.execute("""INSERT INTO gold.decisioning_customer VALUES
        ('G-000001','mass','ON',60,'employed','professional','54','biweekly',72000,true,'rent',1500,2,5200,1300,35,'31-60',640,'bureau_300_850',TIMESTAMP '2026-01-01 00:00:00',TIMESTAMP '2026-01-02 00:00:00'),
        ('G-000001','mass','ON',68,'employed','professional','54','biweekly',72000,true,'rent',1500,2,5200,1300,35,'31-60',655,'bureau_300_850',TIMESTAMP '2026-09-01 00:00:00',TIMESTAMP '2026-09-02 00:00:00'),
        ('G-000002','affluent','QC',120,'self_employed','trades','23','monthly',95000,false,'own',1800,1,900,900,70,'61-90',590,'bureau_300_850',TIMESTAMP '2026-09-01 00:00:00',TIMESTAMP '2026-09-02 00:00:00'),
        ('G-000003','mass','BC',30,'employed','retail','44','weekly',48000,true,'rent',1200,1,12000,2400,15,'1-30',700,'bureau_300_850',TIMESTAMP '2026-09-01 00:00:00',TIMESTAMP '2026-09-02 00:00:00')""")

    con.execute("""INSERT INTO gold.promises_enriched VALUES
        ('PTP-1','G-000001','CASE-1','ACC-1','card','AG-01','phone',TIMESTAMP '2026-08-01 10:00:00',500,DATE '2026-08-10',9,'full',1,0,800,0.62,30,'etransfer',2,DATE '2026-07-28',
            'broken',TIMESTAMP '2026-08-12 00:00:00',0,NULL,'income_delay',true),
        ('PTP-2','G-000001','CASE-1','ACC-1','card','AG-01','phone',TIMESTAMP '2026-09-05 11:00:00',400,DATE '2026-09-12',7,'partial',2,1,800,0.5,33,'etransfer',2,NULL,
            'kept',TIMESTAMP '2026-09-12 00:00:00',400,DATE '2026-09-12',NULL,false),
        ('PTP-3','G-000002','CASE-2','ACC-3','loan','AG-02','sms',TIMESTAMP '2026-09-10 09:00:00',900,DATE '2026-09-20',10,'full',1,0,900,1.0,65,'pad',0,NULL,
            'broken',TIMESTAMP '2026-09-21 00:00:00',0,NULL,'disputed_amount',true),
        ('PTP-4','G-000003','CASE-3','ACC-4','card','AG-01','phone',TIMESTAMP '2026-09-25 14:00:00',600,DATE '2026-10-05',10,'full',1,0,2400,0.25,12,'etransfer',2,NULL,
            'open',NULL,NULL,NULL,NULL,NULL)""")

    con.execute("""INSERT INTO gold.customer_safeguards VALUES
        ('G-000001',false,false,false,false,false,false,false,false,'yes','yes','yes','yes','en',TIMESTAMP '2026-09-28 06:00:00'),
        ('G-000002',true,false,false,false,false,false,false,false,'yes','no','yes','yes','fr',TIMESTAMP '2026-09-28 06:00:00'),
        ('G-000003',false,false,false,false,true,true,false,false,'yes','yes','yes','yes','en',TIMESTAMP '2026-09-28 06:00:00')""")

    con.execute("""INSERT INTO gold.bucket_transitions VALUES
        ('ACC-1','G-000001','card',DATE '2026-08-28',DATE '2026-09-28','1-30','31-60',15,35),
        ('ACC-3','G-000002','loan',DATE '2026-08-28',DATE '2026-09-28','31-60','61-90',40,70)""")

    con.execute("""INSERT INTO gold.customer_timeline VALUES
        ('G-000001',TIMESTAMP '2026-08-01 10:00:00','promise','promises_to_pay','PTP-1','ACC-1','AG-01','phone',NULL,NULL),
        ('G-000001',TIMESTAMP '2026-09-04 15:00:00','note','agent_notes','NOTE-9','ACC-1','AG-01','phone','Customer says paycheque is late this month and asked for a few days.',NULL),
        ('G-000003',TIMESTAMP '2026-09-20 10:30:00','transcript','call_transcripts','TR-5','ACC-4','AG-01','phone','Customer mentioned losing hours at work and worry about the card payment.','VS-10000')""")

    con.execute("""INSERT INTO gold.text_features VALUES
        ('G-000003','TR-5',TIMESTAMP '2026-09-20 10:30:00',true,'reduced_income','moderate',false,
            'Customer mentioned losing hours at work and worry about the card payment.',0.82,'text_v1')""")

    card = DecisionCard(
        recommended_action="agent_call", channel="phone", risk_score=0.81,
        top_reasons=[Reason("prior_broken_ptp_count", "Broke a promise recently", 0.31)],
        runner_up_action="sms_reminder", why_beat_runner_up="Higher expected payment after contact cost",
        blocked_actions=[BlockedAction("email", "contact_limit")],
        evidence=[EvidenceQuote("NOTE-9", "2026-09-04", "Customer says paycheque is late this month.")],
        requires_approval=False, summary="High risk of breaking (0.81): recent broken promise.")
    con.execute("INSERT INTO gold.nba_decisions VALUES ('REC-1','G-000001','CASE-1','AG-01',DATE '2026-09-28',"
                "'agent_call','phone',0.81,420.0,false,NULL,?, 'model_v0','snap-1',TIMESTAMP '2026-09-28 06:30:00')",
                [card.to_json()])
    con.execute("INSERT INTO gold.nba_overrides VALUES ('REC-1','AG-01','accepted',NULL,TIMESTAMP '2026-09-28 09:00:00')")
