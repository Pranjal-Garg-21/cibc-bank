"""The overview's state query (tiui/nba_state.py) and nba/review.py give every recommendation the same state."""
from datetime import date, datetime

import duckdb
import pytest

import uikit  # noqa: F401  (puts ui/ on sys.path)
from tiui.nba_state import state_rows
from trustissues.common.interfaces import apply_stubs
from trustissues.features import sources
from trustissues.nba import review

D = date(2026, 9, 28)
RECS = [  # recommendation_id, agent_id, action, requires_approval, approval_reason
    ("R-CALL", "AG-1", "agent_call", False, None),
    ("R-HB", "AG-1", "agent_call", True, "high_balance"),
    ("R-HB2", "AG-1", "agent_call", True, "high_balance"),
    ("R-HARD", "HS-1", "support_call", True, "hardship"),
    ("R-SMS", None, "sms_reminder", True, "digital_batch"),
    ("R-WAIT", "AG-1", "wait", False, None),
    ("R-WAIT2", "AG-1", "wait", False, None),
    ("R-DONE", "AG-1", "agent_call", False, None),
]


@pytest.fixture
def con():
    c = duckdb.connect()
    apply_stubs(c)
    c.execute(f"CREATE SCHEMA IF NOT EXISTS {sources.SCHEMA}")
    cols = {src for src, _ in sources.SOURCES["agents"].columns.values()}
    c.execute(f"CREATE TABLE {sources.SCHEMA}.agents ({', '.join(f'{x} VARCHAR' for x in sorted(cols))})")
    for a, role in (("AG-1", "agent"), ("TL-1", "team_lead"), ("HS-1", "hardship_specialist")):
        c.execute(f"INSERT INTO {sources.SCHEMA}.agents (agent_id, role, status) VALUES (?, ?, 'active')", [a, role])
    for rec, agent, action, needs, why in RECS:
        c.execute("""INSERT INTO gold.nba_decisions VALUES (?, 'C-1', ?, ?, ?, ?, NULL, 0.7, 5.0, ?, ?, '{}', 'm', 's',
                     TIMESTAMP '2026-09-28 06:00:00')""", [rec, f"CS-{rec}", agent, D, action, needs, why])
    return c


def _both(con):
    backend = dict(review.status(con, D).select("recommendation_id, state").fetchall())
    ui = {r["recommendation_id"]: r["state"] for r in state_rows(con, D)}
    return backend, ui


def test_overview_and_backend_agree_on_every_state(con):
    t = lambda h: datetime(2026, 9, 28, h)  # noqa: E731
    review.record(con, "R-CALL", "AG-1", "accepted", ts=t(9))                       # ready
    review.record(con, "R-HB", "AG-1", "accepted", ts=t(9))                         # waiting_approval
    review.record(con, "R-HB2", "TL-1", "rejected", "card_facts_wrong", ts=t(9))    # rejected, then overridden:
    review.record(con, "R-HB2", "AG-1", "overridden", "other", ts=t(10))            # blocked
    review.record(con, "R-WAIT", "AG-1", "accepted", ts=t(9))                       # done
    review.record(con, "R-DONE", "AG-1", "accepted", ts=t(9))
    review.record(con, "R-DONE", "AG-1", "completed", "no_answer", ts=t(10))        # done
    backend, ui = _both(con)
    assert backend == ui
    assert backend == {"R-CALL": "ready", "R-HB": "waiting_approval", "R-HB2": "blocked", "R-HARD": "waiting_agent",
                       "R-SMS": "waiting_approval", "R-WAIT": "done", "R-WAIT2": "ready", "R-DONE": "done"}
