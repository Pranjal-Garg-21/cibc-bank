"""Action valuation, explanations, capacity allocation and the queue's per-case decision."""
import json
from dataclasses import fields
from datetime import date

import duckdb
import pytest

from trustissues.common import config
from trustissues.common.interfaces import DecisionCard, HardshipOption, Reason, apply_stubs
from trustissues.nba import actions, allocate, explain, hardship, rules
from trustissues.nba.queue import COLUMNS, CaseInput, Context, decide_case, plan_case

COSTS = {"phone": 2.4, "sms": 0.01, "email": 0.001, "push": 0.001, "mail": 1.1}
ALL_CONSENT = {c: True for c in rules.CHANNELS}
CTX = Context(as_of=date(2026, 9, 28), costs=COSTS, effect=0.01, base_risk=0.5, high_balance=5000.0,
              model_version="case_nocure_v0+ptp_break_v0", snapshot_id="FS-test")


def _case(**kw):
    base = dict(case_id="CS-1", customer_id="C-1", assigned_agent_id="AG-1", agent_certified=False, amount=500.0,
                flags=rules.CaseFlags(consent=dict(ALL_CONSENT)), signal=hardship.HardshipSignal(), risk=0.8,
                reasons=[Reason("dpd_now", "47 days past due now", 0.4)], team="T1")
    return CaseInput(**{**base, **kw})


# ---- valuation -------------------------------------------------------------------------------------------

def test_value_is_effect_times_relative_risk_times_reach_times_amount_minus_cost():
    v = actions.value_actions(list(actions.CATALOGUE), 0.8, 500.0, COSTS, effect=0.01, base_risk=0.5,
                              reach={"agent_call": 1.5})
    assert v["wait"] == 0.0
    assert v["agent_call"] == pytest.approx(0.01 * (0.8 / 0.5) * 1.5 * 500 - 2.4)
    assert v["letter"] == pytest.approx(0.01 * 1.6 * 500 - 1.1)  # no reach data: average


def test_no_effect_or_no_risk_means_wait():
    for effect, risk in ((0.0, 0.9), (0.01, 0.0), (-0.01, 0.9)):
        v = actions.value_actions(list(actions.CATALOGUE), risk, 500.0, COSTS, effect=effect)
        assert actions.choose(list(v), v) == "wait"


def test_reach_is_relative_to_average_and_shrunk_halfway():
    r = actions.reach({"email_open_rate_30d": 0.0, "call_rpc_rate_30d": 1.0, "sms_click_rate_30d": None},
                      {"email_open_rate_30d": 0.4, "call_rpc_rate_30d": 0.5, "sms_click_rate_30d": 0.1,
                       "push_open_rate_30d": 0.2})
    assert r["email_reminder"] == pytest.approx(0.5)   # never opens: half the average effect
    assert r["agent_call"] == pytest.approx(1.5)       # always answers
    assert r["sms_reminder"] == 1.0 and r["push_reminder"] == 1.0  # no recent contacts: average


def test_cheapest_channel_wins_at_equal_reach_and_reach_can_justify_a_call():
    assert decide_case(_case(), CTX)["action"] in ("email_reminder", "push_reminder")
    phone_person = _case(reach={"email_reminder": 0.5, "push_reminder": 0.5, "sms_reminder": 0.5,
                                "agent_call": 1.5}, amount=2000.0)
    assert decide_case(phone_person, CTX)["action"] == "agent_call"


# ---- decision card ---------------------------------------------------------------------------------------

def test_card_fits_the_gold_stub_and_the_decision_card_shape():
    row = decide_case(_case(promise_risk=0.83), CTX)
    con = duckdb.connect()
    apply_stubs(con)
    con.execute(f"INSERT INTO gold.nba_decisions VALUES ({', '.join('?' * len(COLUMNS))})", [row[c] for c in COLUMNS])
    assert con.execute("SELECT count(*) FROM gold.nba_decisions").fetchone()[0] == 1
    card = json.loads(row["decision_card"])
    assert set(card) == {f.name for f in fields(DecisionCard)}
    assert card["summary"].startswith("High risk of not being paid up in 30 days (0.80): 47 days past due now.")
    assert "Open promise: 0.83 risk of breaking." in card["summary"]


def test_hard_stop_case_waits_and_says_why():
    row = decide_case(_case(flags=rules.CaseFlags(consent=dict(ALL_CONSENT), cease_contact=True)), CTX)
    card = json.loads(row["decision_card"])
    assert row["action"] == "wait" and row["expected_value"] == 0.0
    assert {b["rule"] for b in card["blocked_actions"]} == {"cease_contact"}
    assert "Contact blocked (cease contact)" in card["summary"]


def test_experiment_holdout_cases_are_left_alone():
    row = decide_case(_case(flags=rules.CaseFlags(consent=dict(ALL_CONSENT), experiment_holdout=True)), CTX)
    assert row["action"] == "wait"
    assert {b["rule"] for b in json.loads(row["decision_card"])["blocked_actions"]} == {"experiment_holdout"}


def test_hardship_gets_a_support_call_with_its_programmes_and_never_a_reminder():
    phone_person = dict(reach={"email_reminder": 0.5, "push_reminder": 0.5, "sms_reminder": 0.5, "agent_call": 1.5},
                        amount=2000.0)
    plain = decide_case(_case(**phone_person), CTX)
    assert plain["action"] == "agent_call"
    opts = [HardshipOption("HP-CARD-SKIP", "2026.1", "Skip a payment", "§4.2", "agent")]
    hard = decide_case(_case(signal=hardship.HardshipSignal("clear"), options=opts, **phone_person), CTX)
    card = json.loads(hard["decision_card"])
    assert hard["action"] == "support_call" and hard["channel"] == "phone"
    assert hard["requires_approval"] and hard["approval_reason"] == "hardship"
    assert card["support_reason"] == "offer_programme"
    assert [o["program_id"] for o in card["hardship_options"]] == ["HP-CARD-SKIP"]
    assert "§4.2" in card["why_beat_runner_up"] and "Skip a payment" in card["summary"]
    # no phone consent: the support call is blocked, so it waits (no reminder instead), with no approval needed
    email_only = rules.CaseFlags(consent={"email": True, "push": True})
    waiting = decide_case(_case(signal=hardship.HardshipSignal("clear"), flags=email_only), CTX)
    assert waiting["action"] == "wait" and not waiting["requires_approval"]
    assert waiting["agent_id"] is None  # uncertified agent: the queue assigns a certified owner
    assert "consent" in json.loads(waiting["decision_card"])["why_beat_runner_up"]
    certified = decide_case(_case(signal=hardship.HardshipSignal("clear"), agent_certified=True, flags=email_only), CTX)
    assert certified["agent_id"] == "AG-1"


def test_running_plan_waits_without_approval():
    plan = hardship.HardshipHistory(plan_end=date(2026, 12, 31), ever_offered=True)
    d = decide_case(_case(signal=hardship.HardshipSignal("clear"), history=plan), CTX)
    assert d["action"] == "wait" and not d["requires_approval"]
    assert "plan in place" in json.loads(d["decision_card"])["why_beat_runner_up"]


def test_approval_reasons():
    digital = decide_case(_case(flags=rules.CaseFlags(consent={"sms": True})), CTX)
    assert digital["action"] == "sms_reminder" and digital["approval_reason"] == "digital_batch"
    assert digital["agent_id"] is None
    assert decide_case(_case(amount=20000.0), CTX)["approval_reason"] == "high_balance"
    assert decide_case(_case(amount=20000.0, flags=rules.CaseFlags()), CTX)["approval_reason"] is None  # waits
    vuln = decide_case(_case(signal=hardship.HardshipSignal("possible", vulnerable=True)), CTX)
    assert vuln["approval_reason"] == "vulnerability"


def test_top_reasons_are_the_largest_risk_increasing_features_and_say_what_missing_means():
    reasons = explain.top_reasons(
        {"dpd_now": 47, "broken_ptp_90d": 2, "open_ptp_to_overdue_ratio": None, "days_to_open_ptp_due": None,
         "card_utilisation": 0.9},
        {"dpd_now": 2.0, "broken_ptp_90d": 0.5, "open_ptp_to_overdue_ratio": 0.4, "days_to_open_ptp_due": 0.3,
         "card_utilisation": 0.1})
    assert [r.text for r in reasons] == ["47 days past due now", "2 promises broken in the last 90 days",
                                         "No open promise to pay"]  # the second "no open promise" is dropped


@pytest.mark.parametrize("model", ["ptp_break_v0", "case_nocure_v0"])
def test_every_model_feature_has_a_sentence(model):
    meta = config.p3_dir() / "models" / f"{model}.json"
    if not meta.exists():
        pytest.skip("no trained model")
    feats = json.loads(meta.read_text(encoding="utf-8"))["features"]
    missing = [f for f in feats if f not in explain.TEMPLATES]
    assert not missing, f"no reason template for {missing}"


# ---- capacity --------------------------------------------------------------------------------------------

def _cap(channel=None, calls=None, certified=()):
    calls = calls or {"AG-1": 1, "AG-2": 1, "AG-9": 5}
    return allocate.Capacity(channel=channel or {"phone": 100, "email": 100, "sms": 100, "push": 100, "mail": 100},
                             agent_calls=dict(calls), team={"AG-1": "T1", "AG-2": "T1", "AG-9": "T2"},
                             certified=set(certified))


def _req(order, value, agent="AG-1", team="T1", certified=False):
    return allocate.Request(order=order, value=value, assigned_agent=agent, team=team, needs_certified=certified)


def test_most_valuable_cases_get_scarce_capacity_and_the_rest_fall_back():
    reqs = [_req(["letter", "email_reminder", "wait"], 5.0), _req(["letter", "email_reminder", "wait"], 9.0)]
    out = allocate.allocate(reqs, _cap(channel={"mail": 1, "email": 0}))
    assert out[1].action == "letter"
    assert out[0].action == "wait" and out[0].no_capacity == ["letter", "email_reminder"]


def test_calls_go_to_the_assigned_agent_then_the_team_then_anyone():
    reqs = [_req(["agent_call", "wait"], v) for v in (9, 8, 7, 6)]
    out = allocate.allocate(reqs, _cap(calls={"AG-1": 1, "AG-2": 1, "AG-9": 1}))
    assert [o.agent_id for o in out] == ["AG-1", "AG-2", "AG-9", "AG-1"]
    assert [o.action for o in out] == ["agent_call"] * 3 + ["wait"]
    assert out[3].no_capacity == ["agent_call"]


def test_hardship_calls_only_go_to_certified_agents():
    out = allocate.allocate([_req(["agent_call", "wait"], 9, certified=True)], _cap(certified={"AG-9"}))
    assert out[0].action == "agent_call" and out[0].agent_id == "AG-9"
    out = allocate.allocate([_req(["agent_call", "wait"], 9, certified=True)], _cap(certified=()))
    assert out[0].action == "wait"


def test_support_calls_go_first_to_certified_agents_only():
    reqs = [_req(["agent_call", "wait"], 50.0), _req(["support_call", "wait"], 1.0, certified=True)]
    reqs[1].tier = 0
    out = allocate.allocate(reqs, _cap(calls={"AG-1": 1, "AG-9": 1}, certified={"AG-9"}))
    assert (out[1].action, out[1].agent_id) == ("support_call", "AG-9")  # placed first, certified agent only
    assert (out[0].action, out[0].agent_id) == ("agent_call", "AG-1")


def test_every_flagged_case_gets_a_certified_owner():
    cap = _cap(certified={"AG-2", "AG-9"})
    reqs = [_req(["wait"], 0, agent="AG-2"), _req(["wait"], 0, agent="AG-1", team="T1"),
            _req(["wait"], 0, agent="AG-1", team="T1"), _req(["wait"], 0, agent=None, team="T9")]
    assert allocate.assign_owners(reqs, cap) == ["AG-2", "AG-2", "AG-2", "AG-9"]  # own, team, team, anyone
    assert allocate.assign_owners(reqs, _cap(certified=())) == [None] * 4


def test_capacity_never_makes_a_hardship_case_harsher():
    flags = rules.CaseFlags(consent=dict(ALL_CONSENT))
    plan = plan_case(_case(flags=flags, signal=hardship.HardshipSignal("possible"), amount=3000.0,
                           reach={"agent_call": 2.0}), CTX)
    assert all(actions.harshness(a) <= hardship.MAX_HARSHNESS_IN_HARDSHIP for a in plan.order)
