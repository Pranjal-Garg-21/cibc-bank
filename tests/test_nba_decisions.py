"""Action valuation, explanations and the queue's per-case decision (nba/actions, explain, queue)."""
import json
from dataclasses import fields
from datetime import date

import duckdb

from trustissues.common.interfaces import DecisionCard, Reason, apply_stubs
from trustissues.nba import actions, explain, hardship, rules
from trustissues.nba.queue import COLUMNS, CaseInput, Context, decide_case

RATES = {"31-60": {"wait": 0.14, "push_reminder": 0.15, "email_reminder": 0.22, "sms_reminder": 0.23,
                   "letter": 0.144, "agent_call": 0.30}}
COSTS = {"phone": 2.4, "sms": 0.01, "email": 0.001, "push": 0.001, "mail": 1.1}
ALL_CONSENT = {c: True for c in rules.CHANNELS}
CTX = Context(as_of=date(2026, 9, 28), rates=RATES, costs=COSTS, high_balance=5000.0, model_version="ptp_break_v0",
              snapshot_id="FS-test")


def _case(**kw):
    base = dict(case_id="CS-1", customer_id="C-1", assigned_agent_id="AG-1", agent_certified=False, bucket="31-60",
                amount=500.0, flags=rules.CaseFlags(consent=dict(ALL_CONSENT)), signal=hardship.HardshipSignal(),
                risk=0.8, reasons=[Reason("ptp_to_overdue_ratio", "Promised 40% of the overdue amount", 2.1)])
    return CaseInput(**{**base, **kw})


def test_value_is_risk_times_lift_times_amount_minus_cost():
    v = actions.value_actions(list(actions.CATALOGUE), "31-60", 0.8, 500.0, RATES, COSTS)
    assert v["wait"] == 0.0
    assert abs(v["agent_call"] - (0.8 * (0.30 - 0.14) * 500 - 2.4)) < 1e-9


def test_no_risk_means_no_contact():
    v = actions.value_actions(list(actions.CATALOGUE), "31-60", 0.0, 500.0, RATES, COSTS)
    assert actions.choose(list(v), v) == "wait"


def test_unknown_bucket_or_unrated_action_is_never_chosen():
    assert actions.value_actions(list(actions.CATALOGUE), "999+", 0.9, 500.0, RATES, COSTS) == {"wait": 0.0}
    rates = {"31-60": {"wait": 0.14, "email_reminder": 0.22}}
    v = actions.value_actions(list(actions.CATALOGUE), "31-60", 0.9, 500.0, rates, COSTS)
    assert set(v) == {"wait", "email_reminder"}


def test_payment_rates_fill_wait_with_the_weakest_treatment(tmp_path):
    path = tmp_path / "av.parquet"
    duckdb.connect().execute(f"""COPY (SELECT * FROM (VALUES
        ('outbound_call', 'champion', '31-60', 1000, 10, 0.1, 0.30, 50.0, 1.6),
        ('letter', 'champion', '31-60', 1000, 10, 0.0, 0.14, 20.0, 1.1),
        ('push', 'champion', '31-60', 19, 5, 0.0, 0.90, 9.0, 0.0),
        ('wait', 'no_contact_holdout', '1-30', 600, 600, NULL, 0.17, 40.0, 0.0))
        v(treatment_code, test_cell, dpd_bucket, contacts, cases, promise_rate, paid_30d_rate, avg_paid_30d,
          avg_contact_cost_cad)) TO '{path.as_posix()}' (FORMAT PARQUET)""")
    rates = actions.load_payment_rates(path)
    assert rates["31-60"] == {"agent_call": 0.30, "letter": 0.14, "wait": 0.14}  # push: too few contacts
    assert rates["1-30"] == {"wait": 0.17}


def test_card_fits_the_gold_stub_and_the_decision_card_shape():
    row = decide_case(_case(), CTX)
    con = duckdb.connect()
    apply_stubs(con)
    con.execute(f"INSERT INTO gold.nba_decisions VALUES ({', '.join('?' * len(COLUMNS))})", [row[c] for c in COLUMNS])
    assert con.execute("SELECT action FROM gold.nba_decisions").fetchone()[0] == "agent_call"
    card = json.loads(row["decision_card"])
    assert set(card) == {f.name for f in fields(DecisionCard)}
    assert card["runner_up_action"] == "sms_reminder"
    assert card["summary"].startswith("High risk of breaking (0.80): Promised 40% of the overdue amount.")


def test_hard_stop_case_waits_and_says_why():
    row = decide_case(_case(flags=rules.CaseFlags(consent=dict(ALL_CONSENT), cease_contact=True)), CTX)
    card = json.loads(row["decision_card"])
    assert row["action"] == "wait" and row["expected_value"] == 0.0
    assert {b["rule"] for b in card["blocked_actions"]} == {"cease_contact"}
    assert "Contact blocked (cease contact)" in card["summary"]


def test_hardship_is_gentler_needs_approval_and_leaves_an_uncertified_agent():
    plain = decide_case(_case(), CTX)
    hard = decide_case(_case(signal=hardship.HardshipSignal("possible")), CTX)
    assert actions.harshness(hard["action"]) <= actions.harshness(plain["action"])
    assert hard["requires_approval"] and hard["approval_reason"] == "hardship"
    assert hard["agent_id"] is None  # assigned agent is not certified: route to one who is
    certified = decide_case(_case(signal=hardship.HardshipSignal("possible"), agent_certified=True,
                                  flags=rules.CaseFlags(consent={"phone": True})), CTX)
    assert certified["action"] == "wait" and certified["agent_id"] == "AG-1"


def test_approval_reasons():
    digital = decide_case(_case(flags=rules.CaseFlags(consent={"sms": True})), CTX)
    assert digital["action"] == "sms_reminder" and digital["approval_reason"] == "digital_batch"
    assert digital["agent_id"] is None
    big = decide_case(_case(amount=20000.0), CTX)
    assert big["approval_reason"] == "high_balance"
    vuln = decide_case(_case(signal=hardship.HardshipSignal("possible", vulnerable=True)), CTX)
    assert vuln["approval_reason"] == "vulnerability"


def test_case_without_open_promise_has_no_risk_or_snapshot():
    row = decide_case(_case(risk=None, reasons=[]), CTX)
    assert row["risk_score"] is None and row["feature_snapshot_id"] is None
    assert json.loads(row["decision_card"])["summary"].startswith("No open promise.")


def test_top_reasons_are_the_largest_risk_increasing_features():
    reasons = explain.top_reasons(
        {"ptp_to_overdue_ratio": 0.4, "broken_ptp_90d": 2, "dpd_at_creation": 47, "card_utilisation": 0.9},
        {"ptp_to_overdue_ratio": 2.0, "broken_ptp_90d": 0.5, "dpd_at_creation": -0.3, "card_utilisation": 0.1})
    assert [r.feature for r in reasons] == ["ptp_to_overdue_ratio", "broken_ptp_90d", "card_utilisation"]
    assert [r.text for r in reasons] == ["Promised 40% of the overdue amount", "2 promises broken in the last 90 days",
                                         "Card utilisation 90%"]


def test_every_model_feature_has_a_sentence():
    import pytest

    from trustissues.common import config

    meta = config.p3_dir() / "models" / "ptp_break_v0.json"
    if not meta.exists():
        pytest.skip("no trained model")
    feats = json.loads(meta.read_text(encoding="utf-8"))["features"]
    missing = [f for f in feats if f not in explain.TEMPLATES]
    assert not missing, f"no reason template for {missing}"
