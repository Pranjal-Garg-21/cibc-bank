"""Certified answer shapes cannot lose a grouping or replace a count with a mean."""
from datetime import date

import pytest

from trustissues.ask import metric_matcher, router, service
from trustissues.ask.catalog import MetricCall, Unsupported, build_call
from trustissues.ask.types import UserContext


AS_OF = date(2026, 9, 28)
LEAD = UserContext("shape-review", "team_lead")


@pytest.mark.parametrize("metric,topic", [
    ("broken_promise_rate", "broken-promise rate"),
    ("promise_kept_rate", "promise-kept rate"),
    ("rpc_rate", "RPC rate"),
])
@pytest.mark.parametrize("grouping", [
    "by borrower", "per agent", "for each account", "by branch", "by customer identifier", "per week",
])
def test_unapproved_grain_is_not_silently_dropped(metric, topic, grouping):
    call = build_call(f"Report {topic} for May 2025 {grouping}", metric, AS_OF)
    assert isinstance(call, Unsupported)
    assert "breakdown" in call.reason


@pytest.mark.parametrize("entity", ["borrowers", "agents", "accounts"])
def test_implicit_entity_ranking_is_an_unsupported_breakdown(entity):
    card = service.ask(f"Which individual {entity} have the highest promise-kept rate for May 2025?",
                       LEAD, as_of_date=AS_OF)
    assert card.path == "cannot_answer" and not card.certified and card.sql is None


def test_supported_dimension_cannot_hide_a_second_unsupported_dimension():
    call = build_call("Report broken-promise rate for May 2025 by agent site and per borrower",
                      "broken_promise_rate", AS_OF)
    assert isinstance(call, Unsupported)


@pytest.mark.parametrize("connector", ["and", ",", ", and"])
def test_coordinated_axes_each_go_through_the_dimension_allowlist(connector):
    allowed = build_call(f"Report broken-promise rate for May 2025 by product {connector} queue",
                         "broken_promise_rate", AS_OF)
    assert isinstance(allowed, MetricCall) and set(allowed.dimensions) == {"product", "queue"}
    denied = build_call(f"Report broken-promise rate for May 2025 by product {connector} borrower",
                        "broken_promise_rate", AS_OF)
    assert isinstance(denied, Unsupported)


def test_supported_site_grain_and_existing_unit_denominators_still_work():
    call = build_call("Report broken-promise rate for May 2025 by agent site", "broken_promise_rate", AS_OF)
    assert isinstance(call, MetricCall) and call.dimensions == ("agent_site",)
    for metric, topic in (("contact_attempts_per_case", "Contact attempts per open case"),
                          ("cost_per_contact", "Cost per call"), ("cost_per_rpc", "Cost per RPC")):
        assert isinstance(build_call(f"{topic} for May 2025", metric, AS_OF), MetricCall)


@pytest.mark.parametrize("operation", ["Count of", "Number of", "How many", "Total"])
@pytest.mark.parametrize("population", [
    "early-stage retail cases", "delinquent secured loan accounts", "review queue cases",
])
def test_descriptive_entity_counts_cannot_use_a_mean_match(monkeypatch, operation, population):
    monkeypatch.setattr(router, "match_metric", lambda _: metric_matcher.decide((
        metric_matcher.Candidate("average_dpd", "Average DPD", .95),
        metric_matcher.Candidate("delinquency_rate", "Delinquency", .7),
    )))
    result = router.route(f"{operation} {population} that remain unresolved for May 2025")
    assert result.path == "sql" and result.metric_id is None


def test_entity_count_with_an_average_topic_is_still_a_count():
    result = router.route("Count of cases with average DPD recorded for May 2025")
    assert result.path == "sql" and result.metric_id is None


def test_ambiguous_mean_match_for_an_entity_count_uses_sql(monkeypatch):
    monkeypatch.setattr(router, "match_metric", lambda _: metric_matcher.decide((
        metric_matcher.Candidate("average_dpd", "Average DPD", .70),
        metric_matcher.Candidate("delinquency_rate", "Delinquency", .69),
    )))
    assert router.route("Count of specialist review cases that remain unresolved").path == "sql"


def test_number_of_time_units_is_not_a_population_count(monkeypatch):
    monkeypatch.setattr(router, "match_metric", lambda _: metric_matcher.decide((
        metric_matcher.Candidate("average_dpd", "Average DPD", .95),
        metric_matcher.Candidate("delinquency_rate", "Delinquency", .7),
    )))
    result = router.route("Report the typical number of overdue days for collections cases")
    assert (result.path, result.metric_id) == ("metric", "average_dpd")


def test_count_template_can_still_be_selected_by_semantic_matching(monkeypatch):
    monkeypatch.setattr(router, "match_metric", lambda _: metric_matcher.decide((
        metric_matcher.Candidate("write_off_rate", "Write-off", .95),
        metric_matcher.Candidate("delinquency_rate", "Delinquency", .7),
    )))
    result = router.route("Number of loan accounts removed as uncollectible in May 2025")
    assert (result.path, result.metric_id) == ("metric", "write_off_rate")


def test_catalogue_rejects_a_mean_selected_for_a_count_even_without_the_router():
    call = build_call("Number of review queue accounts awaiting action", "average_dpd", AS_OF)
    assert isinstance(call, Unsupported) and "count" in call.reason.lower()


def test_actual_mean_and_counts_supported_by_a_count_template_remain_certified_shapes():
    mean = router.route("Report the mean number of days past due for open cases by queue")
    assert (mean.path, mean.metric_id) == ("metric", "average_dpd")
    charged = router.route("Count of accounts charged off in May 2025 by product")
    assert (charged.path, charged.metric_id) == ("metric", "write_off_rate")


def test_count_guard_blocks_an_incorrect_route_before_any_data_access(monkeypatch):
    monkeypatch.setattr(service, "_route", lambda *_: router.Route("metric", metric_id="average_dpd"))
    card = service.ask("How many unresolved loan accounts require review?", LEAD, as_of_date=AS_OF)
    assert card.path == "cannot_answer" and not card.certified and card.sql is None
