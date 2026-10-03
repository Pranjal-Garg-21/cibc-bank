from datetime import date, datetime

import duckdb
import pytest

from trustissues.ask.data.semantic import create_semantic_fixtures
from trustissues.ask.service import ask
from trustissues.ask.types import UserContext

AS_OF = date(2026, 9, 28)
LEAD = UserContext("lead1", "team_lead")
AGENT = UserContext("agent1", "agent")


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    create_semantic_fixtures(c)
    n = 0

    def add(day, site, rpc, k):
        nonlocal n
        for _ in range(k):
            n += 1
            c.execute("INSERT INTO gold.semantic_contacts (contact_id, case_id, channel, direction, rpc_flag, agent_site, "
                      "province, contact_hour_local, contact_ts_local) VALUES (?, 'A', 'call', 'outbound', ?, ?, 'ON', 10, ?)",
                      [f"H{n}", rpc, site, datetime(2026, 9, day, 10)])
    # days 21-22: two sites, healthy.  days 23-24: site_b nearly disappears and its RPC rate collapses.
    add(21, "site_a", True, 5); add(21, "site_a", False, 5); add(21, "site_b", True, 8); add(21, "site_b", False, 2)
    add(23, "site_a", True, 5); add(23, "site_a", False, 5); add(23, "site_b", True, 1); add(23, "site_b", False, 3)
    return c


def test_volume_drop_is_located_not_explained(con):
    a = ask("Why did outbound call volume drop on 23-24 September 2026?", LEAD, as_of_date=AS_OF, con=con)
    assert a.path == "drivers" and not a.certified
    top = a.rows[0]
    assert top[1] == "agent_site" and top[2] == "site_b" and top[3] == -6.0
    assert "summary:" in a.answer and "20" in a.answer and "14" in a.answer
    assert any("not connected" in n for n in a.notes)          # no cause claimed
    assert any("22 September" in n or "2026-09-22" in n for n in a.notes)  # the assumed comparison window is stated


def test_rate_change_split_by_segment(con):
    a = ask("Why did the right-party contact rate drop in 23-24 September 2026?", LEAD, as_of_date=AS_OF, con=con)
    assert a.path == "drivers" and a.certified
    site_b = next(r for r in a.rows if r[1] == "agent_site" and r[2] == "site_b")
    assert site_b[3] < 0                                           # site_b pulled the rate down
    total = {r[1]: 0.0 for r in a.rows}
    # rate 12/20 = 60% -> 6/14 ~ 42.9%: reported change matches the metric, not a re-derived number
    assert "60" in a.answer and "42.86" in a.answer


def test_agents_cannot_ask_for_drivers(con):
    a = ask("Why did outbound call volume drop on 23-24 September 2026?", AGENT, as_of_date=AS_OF, con=con)
    assert a.refused and a.refusal_reason == "access"


def test_no_period_asks(con):
    a = ask("Why did outbound call volume drop?", LEAD, as_of_date=AS_OF, con=con)
    assert a.path == "cannot_answer" and "period" in a.answer.lower()


def test_point_in_time_metric_is_not_compared(con):
    a = ask("Why did the delinquency rate rise in September 2026?", LEAD, as_of_date=AS_OF, con=con)
    assert a.path == "cannot_answer"


def test_why_without_a_measurable_thing_stays_incident(con):
    a = ask("Why did customers get upset in September 2026?", LEAD, as_of_date=AS_OF, con=con)
    assert a.path in ("cannot_answer",)



def test_small_change_note_and_no_shares_on_a_flat_metric():
    from trustissues.ask import drivers as D
    c = duckdb.connect(":memory:")
    create_semantic_fixtures(c)
    n = 0
    for month, rate_a, rate_b in [(8, 5, 5), (9, 5, 5)]:  # identical rates and volumes in both months
        for site, hits in (("site_a", rate_a), ("site_b", rate_b)):
            for k in range(10):
                n += 1
                c.execute("INSERT INTO gold.semantic_contacts (contact_id, case_id, channel, direction, rpc_flag, agent_site, "
                          "province, contact_hour_local, contact_ts_local) VALUES (?, 'A', 'call', 'outbound', ?, ?, ?, 10, ?)",
                          [f"F{n}", k < hits, site, "ON" if site == "site_a" else "QC", datetime(2026, month, 10)])
    a = ask("Why did the right-party contact rate change in September 2026?", LEAD, as_of_date=AS_OF, con=c)
    assert a.path == "drivers"
    assert any("overall change is small" in x for x in a.notes)
    assert all(r[a.columns.index("share_of_change")] is None for r in a.rows)


def test_drivers_answer_records_every_query_that_ran(con):
    a = ask("Why did outbound call volume drop on 23-24 September 2026?", LEAD, as_of_date=AS_OF, con=con)
    assert a.sql.count("-- query ") == 6                      # 3 dimensions x 2 periods
    assert a.sql.count("-- parameters:") == 6 and "semantic_contacts" in a.sql
    assert "2026-09-23" in a.sql and "2026-09-21" in a.sql    # both periods are visible to a reviewer


def test_metric_answer_records_the_executed_statement_and_parameters(con):
    from trustissues.ask.benchmark import run
    q = {"question_id": "Q1", "as_of_date": "2026-09-28",
         "question_text": "What was the right-party contact rate on outbound calls in September 2026, by local hour of day?"}
    row = run([q], con=con)[0]
    assert "semantic_contacts" in row["sql_or_sources"] and "-- parameters:" in row["sql_or_sources"]
    assert "start: 2026-09-01" in row["sql_or_sources"] and "LIMIT" in row["sql_or_sources"]  # the statement that actually ran
