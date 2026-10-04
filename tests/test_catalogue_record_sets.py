"""Grouping grammar and relational populations, using synthetic query fragments."""
from datetime import date

import pytest

from trustissues.ask import catalog, metric_matcher, router, service
from trustissues.ask.catalog import MetricCall, Unsupported


AS_OF = date(2026, 9, 28)


@pytest.mark.parametrize("clause", [
    "keeping the reporting window fixed", "while keeping the reporting window fixed",
    "including a reconciliation note", "leaving the presentation unchanged",
    "presentation follows the standard format",
])
@pytest.mark.parametrize("axes,expected", [
    ("channel", ("channel",)),
    ("province and channel", ("province", "channel")),
    ("agent site, province and channel", ("agent_site", "province", "channel")),
])
def test_trailing_clauses_do_not_invent_axes(clause, axes, expected):
    result = catalog.build_call(f"RPC rate in July 2025 by {axes}, {clause}", "rpc_rate", AS_OF)
    assert isinstance(result, MetricCall)
    assert result.dimensions == expected


@pytest.mark.parametrize("axes", ["queue", "province and queue", "province, queue", "district", "province and district"])
def test_real_unsupported_axes_still_reject(axes):
    result = catalog.build_call(f"RPC rate in July 2025 by {axes}", "rpc_rate", AS_OF)
    assert isinstance(result, Unsupported)


@pytest.mark.parametrize("entity", ["customers", "accounts"])
@pytest.mark.parametrize("operation", ["Total unique", "Count of distinct", "Number of"])
@pytest.mark.parametrize("condition", [
    "with payments and disputes", "that had calls and payments", "where contacts also exist",
])
def test_entity_intersections_bypass_metric_retrieval(monkeypatch, entity, operation, condition):
    def forbidden(_):
        raise AssertionError("Relational populations must not enter metric matching")
    monkeypatch.setattr(router, "match_metric", forbidden)
    question = f"{operation} {entity} {condition} during July 2025, for each province"
    assert router.route(question).path == "sql"
    assert service._route(question, object()).path == "sql"


@pytest.mark.parametrize("ratio", ["Percentage", "Proportion", "Share"])
@pytest.mark.parametrize("absence", ["without payments", "with zero calls", "that never received contacts", "with no disputes"])
def test_absent_related_records_bypass_metric_retrieval(monkeypatch, ratio, absence):
    def forbidden(_):
        raise AssertionError("An absence ratio is not a catalogue clarification")
    monkeypatch.setattr(router, "match_metric", forbidden)
    assert router.route(f"{ratio} of active accounts {absence} during July 2025").path == "sql"


def test_unlike_quantities_are_not_offered_as_metric_choices(monkeypatch):
    monkeypatch.setattr(router, "match_metric", lambda _: metric_matcher.decide((
        metric_matcher.Candidate("average_dpd", "Average DPD", .8),
        metric_matcher.Candidate("payroll_delay_share", "Payroll delay", .79),
    )))
    result = router.route("Proportion exhibiting a seasonal pattern")
    assert result.path == "cannot_answer" and result.clarification is None


@pytest.mark.parametrize("question,metric", [
    ("Promise-kept rate during July 2025 by agent site", "promise_kept_rate"),
    ("RPC rate during July 2025 by province", "rpc_rate"),
    ("Hardship rate for cases opened in July 2025 by queue", "hardship_rate"),
    ("Delinquency share of active card accounts by card product", "delinquency_rate"),
    ("Mean calls for every collections case during July 2025", "contact_attempts_per_case"),
])
def test_certified_quantities_do_not_become_set_queries(question, metric):
    assert not catalog.requests_record_set(question)
    assert router.route(question).metric_id == metric


def test_policy_still_precedes_relational_population():
    assert router.route("Should agents call customers with no payments under policy?").path == "rag_corpus"
