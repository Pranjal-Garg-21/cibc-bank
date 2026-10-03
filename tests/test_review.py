"""Human review (nba/review.py): who may decide what, and where each recommendation stands."""
from datetime import date, datetime

import duckdb
import pytest

from trustissues.common.interfaces import apply_stubs
from trustissues.features import sources
from trustissues.nba import review

D = date(2026, 9, 28)
T0 = datetime(2026, 9, 28, 9, 0)
AGENTS = [  # agent_id, role, status
    ("AG-1", "agent", "active"), ("AG-2", "senior_agent", "active"), ("TL-1", "team_lead", "active"),
    ("HS-1", "hardship_specialist", "active"), ("TL-OLD", "team_lead", "inactive"),
]
RECS = [  # recommendation_id, agent_id, action, channel, requires_approval, approval_reason
    ("R-CALL", "AG-1", "agent_call", "phone", False, None),
    ("R-HB", "AG-1", "agent_call", "phone", True, "high_balance"),
    ("R-HARD", "HS-1", "support_call", "phone", True, "hardship"),
    ("R-SMS1", None, "sms_reminder", "sms", True, "digital_batch"),
    ("R-SMS2", None, "sms_reminder", "sms", True, "digital_batch"),
    ("R-WAIT", "AG-2", "wait", None, False, None),
]


@pytest.fixture
def con():
    c = duckdb.connect()
    apply_stubs(c)
    c.execute(f"CREATE SCHEMA IF NOT EXISTS {sources.SCHEMA}")
    cols = {src for src, _ in sources.SOURCES["agents"].columns.values()}
    c.execute(f"CREATE TABLE {sources.SCHEMA}.agents ({', '.join(f'{x} VARCHAR' for x in sorted(cols))})")
    for a, role, status in AGENTS:
        c.execute(f"INSERT INTO {sources.SCHEMA}.agents (agent_id, role, status) VALUES (?, ?, ?)", [a, role, status])
    for rec, agent, action, channel, needs, why in RECS:
        c.execute("""INSERT INTO gold.nba_decisions VALUES (?, 'C-1', ?, ?, ?, ?, ?, 0.7, 5.0, ?, ?, '{}', 'm', 's',
                     TIMESTAMP '2026-09-28 06:00:00')""", [rec, f"CS-{rec}", agent, D, action, channel, needs, why])
    return c


def _states(con):
    return dict(review.status(con, D).select("recommendation_id, state").fetchall())


def _rejects(con, *args, match=None):
    with pytest.raises(review.ReviewError, match=match):
        review.record(con, *args, ts=T0)


def test_reason_codes_are_required_exactly_when_disagreeing(con):
    _rejects(con, "R-CALL", "AG-1", "overridden", match="needs a reason code")
    _rejects(con, "R-CALL", "AG-1", "accepted", "other", match="takes no reason code")
    _rejects(con, "R-CALL", "AG-1", "overridden", "felt_like_it", match="unknown reason code")
    _rejects(con, "R-CALL", "AG-1", "maybe", match="unknown decision")
    _rejects(con, "R-HB", "TL-1", "rejected", match="needs a reason code")
    assert review.record(con, "R-CALL", "AG-1", "overridden", "customer_already_paid", ts=T0)["decision"] == "overridden"


def test_only_the_assigned_agent_decides_and_batches_have_no_agent(con):
    _rejects(con, "R-CALL", "AG-2", "accepted", match="assigned to AG-1")
    _rejects(con, "R-SMS1", "AG-1", "accepted", match="no assigned agent")
    _rejects(con, "R-NOPE", "AG-1", "accepted", match="no recommendation")
    _rejects(con, "R-CALL", "NOBODY", "accepted", match="not an active member")


def test_approvers_by_reason(con):
    _rejects(con, "R-HB", "AG-1", "approved", match="needs a team_lead")
    _rejects(con, "R-HB", "HS-1", "approved", match="needs a team_lead")  # specialists approve hardship only
    _rejects(con, "R-HB", "TL-OLD", "approved", match="not an active member")
    _rejects(con, "R-CALL", "TL-1", "approved", match="does not need approval")
    review.record(con, "R-HARD", "HS-1", "approved", ts=T0)
    review.record(con, "R-HB", "TL-1", "approved", ts=T0)
    assert _states(con)["R-HARD"] == "waiting_agent"  # a support call is a call: its agent accepts it too
    review.record(con, "R-HARD", "HS-1", "accepted", ts=T0)
    assert _states(con)["R-HARD"] == "ready"


def test_states_follow_the_decisions(con):
    assert _states(con) == {"R-CALL": "waiting_agent", "R-HB": "waiting_approval", "R-HARD": "waiting_approval",
                            "R-SMS1": "waiting_approval", "R-SMS2": "waiting_approval", "R-WAIT": "ready"}
    review.record(con, "R-CALL", "AG-1", "accepted", ts=T0)
    review.record(con, "R-HB", "TL-1", "approved", ts=T0)
    review.record(con, "R-HARD", "TL-1", "rejected", "hardship_disclosed", ts=T0)
    review.record(con, "R-WAIT", "AG-2", "overridden", "customer_requested_channel", ts=T0)
    s = _states(con)
    assert s["R-CALL"] == "ready"
    assert s["R-HB"] == "waiting_agent"  # approved, but the agent has not accepted the call yet
    assert s["R-HARD"] == "blocked"
    assert s["R-WAIT"] == "overridden"


def test_latest_decision_wins_and_history_is_kept(con):
    review.record(con, "R-CALL", "AG-1", "accepted", ts=T0)
    review.record(con, "R-CALL", "AG-1", "overridden", "too_much_contact", ts=datetime(2026, 9, 28, 11, 0))
    assert _states(con)["R-CALL"] == "overridden"
    assert con.execute("SELECT count(*) FROM gold.nba_overrides WHERE recommendation_id = 'R-CALL'").fetchone()[0] == 2


def test_batch_approval_covers_the_open_digital_batch_only(con):
    with pytest.raises(review.ReviewError, match="team lead"):
        review.approve_batch(con, "AG-1", D, ts=T0)
    with pytest.raises(review.ReviewError, match="not in the digital batch"):
        review.approve_batch(con, "TL-1", D, {"R-HB": "other"}, ts=T0)
    assert review.approve_batch(con, "TL-1", D, {"R-SMS2": "contact_details_wrong"}, ts=T0) == \
        {"approved": 1, "rejected": 1}
    s = _states(con)
    assert (s["R-SMS1"], s["R-SMS2"], s["R-HB"]) == ("ready", "blocked", "waiting_approval")
    # a second sign-off does not touch what was already decided
    assert review.approve_batch(con, "TL-1", D, ts=datetime(2026, 9, 28, 10, 0)) == {"approved": 0, "rejected": 0}


def test_weekly_summary(con):
    review.record(con, "R-CALL", "AG-1", "overridden", "hardship_disclosed", ts=T0)
    review.record(con, "R-HB", "AG-1", "accepted", ts=T0)
    review.approve_batch(con, "TL-1", D, {"R-SMS1": "too_much_contact"}, ts=T0)
    s = review.summary(con, D, D)
    assert s["recommendations"] == 6 and s["agent_decisions"] == 2
    assert s["override_rate"] == pytest.approx(0.5)
    assert s["overrides_by_reason"] == {"hardship_disclosed": 1}
    assert s["safeguard_overrides"] == [("R-CALL", "CS-R-CALL", "hardship_disclosed")]
    assert s["rejections_by_reason"] == {"too_much_contact": 1}
    assert s["still_pending"] == {"high_balance": 1, "hardship": 1}
