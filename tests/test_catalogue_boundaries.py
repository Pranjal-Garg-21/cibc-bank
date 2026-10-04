"""Requested units, populations and temporal capabilities, with synthetic wording."""
from datetime import date, datetime

import duckdb
import pytest

from trustissues.ask import router, service
from trustissues.ask.catalog import MetricCall, Unsupported, build_call
from trustissues.ask.data.semantic import create_semantic_fixtures
from trustissues.ask.types import UserContext


AS_OF = date(2026, 9, 28)
LEAD = UserContext("boundary-lead", "team_lead")


@pytest.mark.parametrize("unit", ["contacts", "attempts", "touches", "calls", "dials"])
@pytest.mark.parametrize("wording", [
    "Give the mean number of {unit} for every open case during September 2026",
    "Report {unit} per collections case during September 2026",
])
def test_contact_count_units_win_before_embedding_retrieval(monkeypatch, unit, wording):
    def forbidden(_):
        raise AssertionError("An explicit quantity should not need embedding retrieval")
    monkeypatch.setattr(router, "match_metric", forbidden)
    question = wording.format(unit=unit)
    r = router.route(question)
    assert (r.path, r.metric_id) == ("metric", "contact_attempts_per_case")
    assert isinstance(build_call(question, r.metric_id, AS_OF), MetricCall)


def test_contact_quantity_is_not_overridden_by_dpd_context():
    r = router.route("Mean touches per case, with average DPD supplied as context, in September 2026")
    assert r.metric_id == "contact_attempts_per_case"


@pytest.mark.parametrize("question,metric", [
    ("Mean days past due of cases with contact attempts", "average_dpd"),
    ("Contact cost per case in September 2026", None),
])
def test_other_measurements_do_not_become_contact_counts(question, metric):
    assert router.route(question).metric_id == metric


def test_wrong_model_metric_for_contact_count_stops_before_execution(monkeypatch):
    monkeypatch.setattr(service, "route", lambda _: router.Route("metric", metric_id="average_dpd"))
    card = service.ask("Mean outbound attempts for each open case in September 2026", LEAD, as_of_date=AS_OF)
    assert card.path == "cannot_answer" and not card.certified and card.sql is None


def test_contact_quantity_executes_contact_formula_on_fixtures():
    with duckdb.connect(":memory:") as con:
        create_semantic_fixtures(con)
        con.execute("INSERT INTO gold.semantic_cases (case_id,case_open_date,current_dpd) VALUES ('X',DATE '2026-08-01',40),('Y',DATE '2026-08-01',80)")
        for i in range(3):
            con.execute("INSERT INTO gold.semantic_contacts (contact_id,case_id,channel,direction,contact_ts_local) VALUES (?, 'X','call','outbound',?)",
                        [str(i), datetime(2026, 9, 8)])
        card = service.ask("Report mean attempts for every open case during September 2026", LEAD, as_of_date=AS_OF, con=con)
    assert card.certified and card.metric_id == "contact_attempts_per_case"
    assert card.rows[0][0] == 1.5  # three contacts / two cases, including zero attempts


@pytest.mark.parametrize("metric,question", [
    ("delinquency_rate", "Delinquency percentage in November 2024 by product"),
    ("average_dpd", "Mean DPD for February 2025 by queue"),
    ("average_dpd", "Mean DPD as of 2026-09-13"),
    ("average_dpd", "Mean DPD as of 1999-01-05"),
    ("delinquency_rate", "Delinquency rate on 9 September 2026"),
    ("delinquency_rate", "Delinquency rate last quarter"),
    ("average_dpd", "Mean DPD during July-September 2026"),
    ("average_dpd", "Mean DPD in January 2027"),
    ("cure_rate", "Cure rate for cases opened in February 2015 within 30 days"),
    ("cure_rate", "Cure rate in January 2027"),
    ("roll_rate", "Roll rate from end of September 2023 to end of March 2024"),
    ("roll_rate", "Roll rate from end of August 2025 to end of September 2025"),
    ("roll_rate", "Roll rate from end of March 2026 to end of September 2026"),
])
def test_unavailable_periods_are_unsupported_without_execution(metric, question):
    result = build_call(question, metric, AS_OF)
    assert isinstance(result, Unsupported) and result.reason
    card = service.ask(question, LEAD, as_of_date=AS_OF)
    assert card.path == "cannot_answer" and not card.certified and card.sql is None


@pytest.mark.parametrize("metric,question", [
    ("delinquency_rate", "Delinquency rate currently by product"),
    ("average_dpd", "Mean DPD in September 2026 by queue"),
    ("roll_rate", "Roll rate between end of December 2024 and end of March 2025"),
    ("cure_rate", "Cure rate for cases opened in April 2025 within 30 days"),
    ("cure_rate", "Cure rate in September 2026"),
])
def test_supported_current_snapshots_and_event_history_are_preserved(metric, question):
    assert isinstance(build_call(question, metric, AS_OF), MetricCall)


@pytest.mark.parametrize("population", [
    "for case CS-2026-234567",
    "where customer_id = 'C-417'",
    "for customer G42",
    "for account ACC-417",
    "for agent AG-29",
    "for customer Mira Sen",
    "for customer mira sen",
    "for borrower named 'Ravi Patel'",
    "assigned to agent Z-17",
    "where loan_id = 'L-417'",
])
def test_named_population_is_never_dropped_from_portfolio_metric(population):
    question = f"Promise-kept rate in April 2025 {population}"
    result = build_call(question, "promise_kept_rate", AS_OF)
    assert isinstance(result, Unsupported) and "population" in result.reason
    card = service.ask(question, LEAD, as_of_date=AS_OF)
    assert card.path == "cannot_answer" and not card.certified and card.sql is None


@pytest.mark.parametrize("metric", ["broken_promise_rate", "rpc_rate", "average_dpd", "cure_rate"])
def test_entity_filter_guard_applies_to_any_selected_portfolio_metric(metric):
    result = build_call("Report a statistic currently for account #784", metric, AS_OF)
    assert isinstance(result, Unsupported) and "population" in result.reason


def test_customer_call_path_and_portfolio_groupings_are_preserved():
    assert router.route("Describe the recorded conversation for case CS-2026-234567").path == "rag_customer"
    assert isinstance(build_call("Broken-promise rate in April 2025 by agent site", "broken_promise_rate", AS_OF), MetricCall)
    assert isinstance(build_call("Mean DPD by customer province", "average_dpd", AS_OF), MetricCall)


def test_invalid_explicit_snapshot_date_returns_unsupported():
    assert isinstance(build_call("Delinquency rate as of 2026-02-31", "delinquency_rate", AS_OF), Unsupported)


def test_certified_agent_cure_questions_use_the_randomised_pilot():
    """BQ-014. Only the pilot randomised agents to cases, so only it compares certified agents fairly."""
    call = build_call("In the randomised pilot, do agents certified for vulnerable customers achieve a higher 30-day "
                      "cure rate on hardship cases?", "cure_rate", AS_OF)
    assert isinstance(call, MetricCall) and call.variant == "certification_pilot" and call.dimensions == ()
    assert call.parameters == {"hardship_only": True, "observation_end": "2026-09-29"}
    assert build_call("Do certified agents cure more cases?", "cure_rate", AS_OF).parameters["hardship_only"] is False
    # a vulnerable-customer or pilot question that is not about agent certification still has no reviewed variant
    assert isinstance(build_call("What is the cure rate of vulnerable customers in September 2026?", "cure_rate", AS_OF),
                      Unsupported)


def test_pilot_effect_states_direction_size_and_chance():
    rows = [("certified", 4.0575, 48, 1183), ("not_certified", 2.4463, 33, 1349)]
    note = service.pilot_effect(rows)
    assert "is higher" in note and "+1.61 points" in note and "z = 2.30" in note and "unlikely to be chance" in note
    assert "not distinguishable from chance" in service.pilot_effect([("certified", 5., 1, 20), ("not_certified", 0., 0, 20)])
    assert service.pilot_effect([("certified", 5., 1, 20)]) is None  # one group alone has no comparison


def test_days_to_cure_is_a_duration_not_the_cure_rate():
    """BQ-111: 'average number of days to cure' was answered with the cure rate (a percentage)."""
    r = router.route("For cases that cured between 1 July and 28 September 2026, what was the average number of days "
                     "to cure, by primary product?")
    assert r.path == "sql"
    assert router.route("What was the cure rate for cases opened in August 2026?").path == "metric"


def test_promise_kept_rate_by_the_channel_the_promise_was_taken_on():
    """BQ-112: the promise's own channel (ptp_channel), not the contact channel."""
    call = build_call("What was the promise-kept rate for promises due between 1 and 20 September 2026, by the channel "
                      "the promise was taken on?", "promise_kept_rate", AS_OF)
    assert isinstance(call, MetricCall) and call.dimensions == ("ptp_channel",)
    assert build_call("Promise-kept rate by promise channel in September 2026", "promise_kept_rate", AS_OF).dimensions == ("ptp_channel",)
    assert isinstance(build_call("Promise-kept rate by channel in September 2026", "promise_kept_rate", AS_OF), Unsupported)
