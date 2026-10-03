"""The Streamlit pages run end to end on the real warehouse (Streamlit's AppTest), as a team lead and as an agent.

Needs the warehouse with gold.nba_decisions written (python -m trustissues.nba.queue --write-gold); skipped otherwise.
The app opens the warehouse for writing, so the connection is closed at the end for the tests that follow.
"""
import sys

import duckdb
import pytest

from trustissues.common import config

APP = config.REPO_ROOT / "app"
st_testing = pytest.importorskip("streamlit.testing.v1")


@pytest.fixture(scope="module")
def ui():
    path = config.warehouse_path()
    if not path.exists():
        pytest.skip("no warehouse built")
    try:
        with duckdb.connect(str(path), read_only=True) as c:
            n = c.execute(f"SELECT count(*) FROM gold.nba_decisions WHERE as_of_date = DATE '{config.AS_OF_DATE}'").fetchone()[0]
    except duckdb.Error as e:
        pytest.skip(f"warehouse not readable: {e}")
    if not n:
        pytest.skip("gold.nba_decisions is empty: run python -m trustissues.nba.queue --write-gold")
    sys.path.insert(0, str(APP))
    import ui as module
    yield module
    module._db().close()
    module._db.clear()
    sys.path.remove(str(APP))


def _run(page: str, **state):
    at = st_testing.AppTest.from_file(str(APP / page), default_timeout=120)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def test_home_and_lead_queue(ui):
    assert "next best action" in _run("Home.py").title[0].value
    lead = _run("pages/2_Agent_Queue.py")  # the first staff member listed is a team lead
    assert lead.title[0].value.startswith("Team lead")


def test_agent_queue_and_card(ui):
    c = ui.con()
    agent, rec = c.execute(f"""SELECT agent_id, recommendation_id FROM gold.nba_decisions
                               WHERE as_of_date = DATE '{config.AS_OF_DATE}' AND action = 'agent_call'
                               ORDER BY recommendation_id LIMIT 1""").fetchone()
    q = _run("pages/2_Agent_Queue.py", user_id=agent)
    assert q.title[0].value == "Agent queue"
    card = _run("pages/3_Decision_Card.py", user_id=agent, recommendation_id=rec)
    assert card.title[0].value.startswith("Call · case")
    assert any(b.label == "Accept" for b in card.button)
