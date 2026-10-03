from datetime import date, datetime
from types import SimpleNamespace

import duckdb
import pytest

from trustissues.ask.data.semantic import create_semantic_fixtures
from trustissues.ask.service import ask
from trustissues.ask.sql_fallback import build_system, run_fallback
from trustissues.ask.access import query_policy
from trustissues.ask.types import UserContext

AS_OF = date(2026, 9, 28)
LEAD = UserContext("lead1", "team_lead")
Q = "How many collections cases are open today, by current bucket?"


class ScriptedLLM:
    """Returns queued JSON replies; records the prompts it was given."""
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(data=self.replies.pop(0))


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    create_semantic_fixtures(c)
    for i, (b, out) in enumerate([("1-30", None), ("1-30", None), ("31-60", None), ("61-90", "cured")]):
        c.execute("INSERT INTO gold.semantic_cases (case_id, reported_bucket, outcome) VALUES (?,?,?)", [f"C{i}", b, out])
    return c


GOOD = {"sql": "SELECT reported_bucket AS current_bucket, COUNT(*) AS open_cases FROM gold.semantic_cases "
                "WHERE outcome IS NULL GROUP BY reported_bucket", "reason": ""}


def test_prompt_has_schema_and_no_row_data(con):
    s = build_system(AS_OF)
    assert "gold.semantic_cases" in s and "reported_bucket" in s and "2026-09-28" in s
    assert "C0" not in s


def test_generated_query_is_validated_and_labelled(con):
    llm = ScriptedLLM(GOOD)
    # unqualified column names are qualified by the validator; the table is already gold-qualified
    a = ask(Q, LEAD, as_of_date=AS_OF, con=con, llm=llm)
    assert a.path == "sql" and a.certified is False
    assert dict(a.rows) == {"1-30": 2, "31-60": 1}
    assert any("Uncertified" in n for n in a.notes)


def test_one_repair_then_success(con):
    bad = {"sql": "SELECT nope FROM gold.semantic_cases", "reason": ""}
    llm = ScriptedLLM(bad, GOOD)
    a = ask(Q, LEAD, as_of_date=AS_OF, con=con, llm=llm)
    assert a.path == "sql" and len(llm.calls) == 2
    assert "Error:" in llm.calls[1]["user"] and any("repair" in n for n in a.notes)


def test_two_failures_means_cannot_answer(con):
    bad = {"sql": "SELECT nope FROM gold.semantic_cases", "reason": ""}
    llm = ScriptedLLM(bad, bad)
    a = ask(Q, LEAD, as_of_date=AS_OF, con=con, llm=llm)
    assert a.path == "cannot_answer" and len(llm.calls) == 2 and not a.rows


@pytest.mark.parametrize("sql", [
    "DROP TABLE gold.semantic_cases",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT golden_customer_id FROM gold.semantic_cases",
    "SELECT COUNT(*) FROM main.secrets",
])
def test_unsafe_generated_sql_never_runs(con, sql):
    llm = ScriptedLLM({"sql": sql, "reason": ""}, {"sql": sql, "reason": ""})
    fb = run_fallback(Q, llm=llm, policy=query_policy(LEAD), as_of=AS_OF, con=con)
    assert fb.result is None
    assert con.execute("SELECT COUNT(*) FROM gold.semantic_cases").fetchone()[0] == 4


def test_model_can_decline(con):
    llm = ScriptedLLM({"sql": None, "reason": "no such data"})
    a = ask(Q, LEAD, as_of_date=AS_OF, con=con, llm=llm)
    assert a.path == "cannot_answer" and "no such data" in a.answer


def test_no_llm_connected_is_explicit(con):
    a = ask(Q, LEAD, as_of_date=AS_OF, con=con)
    assert a.path == "cannot_answer" and "language model" in a.answer


def test_rows_after_the_as_of_date_are_unreadable(con):
    con.execute("INSERT INTO gold.semantic_contacts (contact_id, case_id, channel, direction, contact_ts_local) VALUES "
                "('X1','A','call','outbound','2026-09-20'), ('X2','A','call','outbound','2026-10-05')")
    sql = {"sql": "SELECT COUNT(*) AS n FROM gold.semantic_contacts", "reason": ""}
    fb = run_fallback("how many contacts?", llm=ScriptedLLM(sql), policy=query_policy(LEAD), as_of=AS_OF, con=con)
    assert fb.result.rows == ((1,),)


def test_model_outage_is_a_clean_cannot_answer(con):
    from trustissues.common.llm import LLMError

    class Down:
        def complete(self, **kw):
            raise LLMError("503")
    a = ask(Q, LEAD, as_of_date=AS_OF, con=con, llm=Down())
    assert a.path == "cannot_answer" and "unavailable" in a.answer


def test_repair_prompt_does_not_carry_quoted_values(con):
    from trustissues.ask.sql_fallback import _redact
    assert "secret" not in _redact("Conversion Error: Could not convert string 'secret-value' to INT64\nLINE 1")
    bad = {"sql": "SELECT CAST(case_id AS INTEGER) FROM gold.semantic_cases", "reason": ""}
    llm = ScriptedLLM(bad, GOOD)
    ask(Q, LEAD, as_of_date=AS_OF, con=con, llm=llm)
    assert "C0" not in llm.calls[1]["user"]
