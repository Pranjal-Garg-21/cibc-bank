import csv
from datetime import date

import pytest

from trustissues.common.config import maple_data_dir
from trustissues.ask.catalog import Unsupported, build_call, detect_metric
from trustissues.ask.router import route

AS_OF = date(2026, 9, 28)


def call(q):
    m = detect_metric(q)
    return build_call(q, m, AS_OF)


def test_bq011_rpc_by_hour_clamped_to_as_of():
    c = call("What was the right-party contact rate on outbound calls in September 2026, by local hour of day?")
    assert c.metric_id == "rpc_rate" and c.dimensions == ("contact_hour_local",)
    assert c.parameters == {"start": date(2026, 9, 1), "end": date(2026, 9, 29)}


def test_bq003_prior_count_capped():
    c = call("How do broken-promise rates change with the number of previously broken promises?")
    assert c.metric_id == "broken_promise_rate" and c.cap_at == 3
    assert c.dimensions == ("prior_broken_ptp_count",)
    assert any("No period" in n for n in c.notes)


def test_bq007_site_and_period():
    c = call("What was the broken-promise rate by agent site for promises due in July-September 2026?")
    assert c.dimensions == ("agent_site",) and c.parameters["start"] == date(2026, 7, 1)


def test_bq017_roll_rate_params():
    c = call("Of accounts that were 1-30 days past due at the end of March 2026, what share had rolled to a worse bucket by the end of June 2026, by product?")
    assert c.metric_id == "roll_rate"
    assert c.parameters["start_snapshot"] == date(2026, 3, 31) and c.parameters["end_snapshot"] == date(2026, 6, 30)
    assert (c.parameters["start_dpd_min"], c.parameters["start_dpd_max"]) == (1, 30)
    assert any("month-first" in n for n in c.notes)  # why our counts exceed the answer key's (it drops those rows)


def test_bq012_card_delinquency():
    c = call("What share of active card accounts are 1 or more days past due today, by card product?")
    assert c.metric_id == "delinquency_rate" and c.dimensions == ("product_code",)
    assert c.parameters == {"snapshot_date": AS_OF, "product": "card"}


def test_bq028_random_only_and_test_cell():
    c = call("Among randomised cases, compare the promise-kept rate of the champion strategy with challenger_A and challenger_B.")
    assert c.dimensions == ("test_cell",) and c.parameters["random_only"] is True


def test_unapproved_dimension_is_unsupported():
    c = call("What is the right-party contact rate in September 2026 by queue?")
    assert isinstance(c, Unsupported)


@pytest.mark.parametrize("q,path", [
    ("Summarise the most recent recorded call on case CS-2026-614370: what did the customer say and agree to?", "rag_customer"),
    ("Is CASL express consent needed to send a collections payment reminder by email?", "rag_corpus"),
    ("A customer tells us they filed a consumer proposal. What should the agent do?", "rag_corpus"),
    ("What is the grace period before a promise to pay is considered broken?", "rag_corpus"),
    ("How many collections cases are open today, by current bucket?", "sql"),
    ("What is the average days past due of open cases by queue?", "metric"),
])
def test_routes(q, path):
    assert route(q).path == path


def test_case_id_alone_is_not_rag_customer():
    assert route("How many contacts did case CS-2026-614370 receive?").path != "rag_customer"


def test_routes_over_all_released_questions():
    path = maple_data_dir() / "data" / "benchmark_questions.csv"
    if not path.exists():
        pytest.skip("benchmark file not available")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    for r in rows:
        if r["metric_id"]:
            assert route(r["question_text"]).path == "metric", r["question_id"]


EXPECTED_NON_METRIC = {
    "BQ-001": "rag_corpus", "BQ-002": "rag_corpus", "BQ-005": "rag_corpus", "BQ-009": "rag_corpus",
    "BQ-019": "rag_corpus", "BQ-020": "rag_corpus", "BQ-026": "rag_corpus", "BQ-033": "rag_corpus",
    "BQ-016": "rag_customer", "BQ-021": "rag_customer", "BQ-022": "rag_customer",
    "BQ-030": "drivers", "BQ-035": "sql",
}
REFUSED = {"BQ-018", "BQ-023", "BQ-024", "BQ-029", "BQ-031"}


def test_every_released_question_takes_the_expected_path():
    from trustissues.ask import refusal
    path = maple_data_dir() / "data" / "benchmark_questions.csv"
    if not path.exists():
        pytest.skip("benchmark file not available")
    for r in csv.DictReader(open(path, encoding="utf-8")):
        qid, text = r["question_id"], r["question_text"]
        if qid in REFUSED:
            assert refusal.check(text), qid
        elif qid in EXPECTED_NON_METRIC:
            assert route(text).path == EXPECTED_NON_METRIC[qid], qid
        else:
            assert route(text).path == "metric", qid


def test_bq006_dispute_comparison_params():
    c = call("How many late-fee disputes were raised between 15 and 22 September 2026, compared with the 8 days before?")
    assert c.metric_id == "dispute_count"
    assert c.parameters == {"start": date(2026, 9, 15), "end": date(2026, 9, 23),
                            "comparison_start": date(2026, 9, 7), "comparison_end": date(2026, 9, 15)}


def test_bq010_out_of_hours_by_province():
    c = call("What share of outbound calls in September 2026 were outside permitted calling hours, by customer province?")
    assert c.metric_id == "out_of_hours_share" and c.dimensions == ("province",)
    assert c.parameters == {"start": date(2026, 9, 1)}


def test_certification_pilot_is_not_answered_with_generic_cure_rate():
    c = call("In the randomised pilot, do agents certified for vulnerable customers achieve a higher 30-day cure rate on hardship cases?")
    assert c.variant == "certification_pilot"  # its own reviewed template, never the generic cure rate
    assert isinstance(call("Does the pilot change the cure rate in September 2026?"), Unsupported)


def test_dispute_without_comparison_asks():
    assert isinstance(call("How many disputes were raised in September 2026?"), Unsupported)


def test_router_eval_baseline_does_not_regress():
    """Floor on the labelled paraphrase set. Raise it when the router improves; never lower it."""
    import subprocess, sys
    from trustissues.common.config import REPO_ROOT
    out = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "eval_router.py"), "--min", "0.47"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout[-800:]
