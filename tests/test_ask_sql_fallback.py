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
        if kw.get("prompt_version", "").startswith("ask_screen"):  # the model screen is not part of the script
            return SimpleNamespace(data={"verdict": "allow", "category": "none"})
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


# ---- implied filters and column hints -------------------------------------------------------------------------

from trustissues.ask.sql_fallback import column_hint, implied_filter_problem  # noqa: E402

CALLS_SQL = "SELECT agent_site, COUNT(*) FROM gold.semantic_contacts WHERE direction = 'outbound' GROUP BY 1"


def test_calls_without_channel_filter_is_rejected():
    msg = implied_filter_problem("How many outbound calls by agent site?", CALLS_SQL)
    assert msg and "channel = 'call'" in msg


def test_filter_present_passes_in_both_spellings():
    q = "How many outbound calls by agent site?"
    ok = "SELECT 1 FROM gold.semantic_contacts WHERE channel = 'call' AND direction = 'outbound'"
    assert implied_filter_problem(q, ok) is None
    assert implied_filter_problem(q, ok.replace("channel = 'call'", "channel IN ('call')")) is None


def test_outbound_without_direction_and_other_tables_unaffected():
    assert "direction" in implied_filter_problem("How many outbound contacts?", "SELECT COUNT(*) FROM gold.semantic_contacts")
    assert implied_filter_problem("How many calls did cases get?", "SELECT COUNT(*) FROM gold.semantic_cases") is None
    assert implied_filter_problem("Calls to the call centre", "SELECT COUNT(*) FROM gold.semantic_contacts WHERE channel = 'call'") is None


def test_unknown_column_hint_names_the_owning_table():
    assert "gold.semantic_cases" in column_hint("ValidationError: Unknown, ambiguous or sensitive column: Unknown column: queue")
    assert "gold.semantic_cases" in column_hint("ValidationError: Column 'queue' could not be resolved. Line: 1, Col: 12")
    assert column_hint("ValidationError: something else") == ""
    assert column_hint("Unknown column: not_a_column") == ""


# ---- label values that do not exist ---------------------------------------------------------------------------

from trustissues.ask.sql_fallback import unknown_label  # noqa: E402


def test_unknown_label_value_is_caught_and_valid_values_pass(con):
    bad = "SELECT COUNT(*) FROM gold.semantic_cases WHERE reported_bucket = 'default'"
    msg = unknown_label(bad, query_policy(LEAD), con=con)
    assert msg and "'default'" in msg and "1-30" in msg
    assert unknown_label("SELECT COUNT(*) FROM gold.semantic_cases WHERE reported_bucket IN ('1-30', '31-60')", query_policy(LEAD), con=con) is None
    assert unknown_label("SELECT COUNT(*) FROM gold.semantic_cases WHERE reported_bucket IN ('1-30', 'nope')", query_policy(LEAD), con=con)


def test_non_label_columns_and_unparseable_sql_are_never_blocked(con):
    assert unknown_label("SELECT COUNT(*) FROM gold.semantic_cases WHERE case_id = 'C-not-there'", query_policy(LEAD), con=con) is None
    assert unknown_label("SELECT FROM WHERE", query_policy(LEAD), con=con) is None


def test_fallback_repairs_after_a_nonexistent_label(con):
    bad = {"sql": "SELECT COUNT(*) AS n FROM gold.semantic_cases WHERE reported_bucket = 'late stage'", "reason": ""}
    llm = ScriptedLLM(bad, GOOD)
    out = run_fallback(Q, llm=llm, policy=query_policy(LEAD), as_of=AS_OF, con=con)
    assert out.result is not None and out.attempts == 2
    assert "never occurs in reported_bucket" in llm.calls[1]["user"]


def test_label_lookup_respects_the_users_row_filters_and_never_leaks_hidden_values(con):
    import dataclasses
    base = query_policy(LEAD)
    scoped = dataclasses.replace(base, required_filters={
        **base.required_filters, "gold.semantic_cases": ("reported_bucket <> '31-60'",)})
    msg = unknown_label("SELECT COUNT(*) FROM gold.semantic_cases WHERE reported_bucket = 'default'", scoped, con=con)
    assert msg and "1-30" in msg and "31-60" not in msg  # the hidden bucket is not named in the prompt text
    hidden = unknown_label("SELECT COUNT(*) FROM gold.semantic_cases WHERE reported_bucket = '31-60'", scoped, con=con)
    assert hidden and "31-60" in hidden.split("Its values are")[0] and "31-60" not in hidden.split("Its values are")[1]


def test_label_lookup_is_skipped_when_the_role_may_not_read_the_column(con):
    import dataclasses
    base = query_policy(LEAD)
    no_cases = dataclasses.replace(base, tables=frozenset(t for t in base.tables if t != "gold.semantic_cases"))
    assert unknown_label("SELECT COUNT(*) FROM gold.semantic_cases WHERE reported_bucket = 'default'", no_cases, con=con) is None


# ---- review findings: trusted filters, filter placement, error redaction ------------------------------------------

def test_as_of_limits_are_added_to_the_roles_own_filters_not_in_place_of_them(con):
    import dataclasses
    base = query_policy(LEAD)
    scoped = dataclasses.replace(base, required_filters={
        **base.required_filters, "gold.semantic_cases": ("queue = 'early_stage'",)})
    llm = ScriptedLLM(GOOD)
    out = run_fallback(Q, llm=llm, policy=scoped, as_of=AS_OF, con=con)
    assert out.result is not None
    assert "queue = 'early_stage'" in out.sql and "1900-01-01" in out.sql  # both the role's filter and the as-of limit


def test_implied_filter_must_be_a_top_level_and_term():
    q = "How many outbound calls by agent site?"
    base = "SELECT COUNT(*) FROM gold.semantic_contacts WHERE direction = 'outbound' AND "
    assert implied_filter_problem(q, base + "(channel = 'call' OR 1 = 1)")
    assert implied_filter_problem(q, base + "NOT channel = 'call'")
    assert implied_filter_problem(q, base + "channel = 'call'") is None
    assert implied_filter_problem(q, base + "gold.semantic_contacts.channel IN ('call')") is None


def test_repair_error_text_carries_no_numbers_from_the_data():
    from trustissues.ask.sql_fallback import _redact
    msg = _redact("Conversion Error: Could not convert 1e+30 to INT32 for value 42.5 at 'x'")
    assert "1e+30" not in msg and "42.5" not in msg and "'x'" not in msg
