"""nba/hardship.py: turning a hardship signal on never makes the recommended action harsher."""
import itertools
import random
from datetime import date, datetime

from trustissues.nba import hardship, rules
from trustissues.nba.actions import CATALOGUE, harshness

SIGNALS = [hardship.HardshipSignal(level, vuln) for level in hardship.LEVELS for vuln in (False, True)]


def _random_case(rng):
    flags = rules.CaseFlags(
        cease_contact=rng.random() < 0.1, deceased=rng.random() < 0.05, insolvency=rng.random() < 0.1,
        open_dispute=rng.random() < 0.1, represented=rng.random() < 0.05, do_not_call=rng.random() < 0.2,
        consent={c: rng.random() < 0.8 for c in rules.CHANNELS}, contacts_last_7d=rng.randint(0, 4))
    values = {a: rng.uniform(-5, 50) for a in CATALOGUE if rng.random() < 0.9}
    return flags, values


def test_hardship_never_harsher_on_random_cases():
    rng = random.Random(20261003)
    off = hardship.HardshipSignal()
    for _ in range(5000):
        flags, values = _random_case(rng)
        base = hardship.decide(flags, values, off)
        for sig in SIGNALS:
            d = hardship.decide(flags, values, sig)
            assert harshness(d.action) <= harshness(base.action), (flags, values, sig, d, base)
            assert d.action in d.rule_result.allowed or d.action == "wait"
            assert d.route_to_certified_agent == sig.active
            if sig.active:
                assert harshness(d.action) <= hardship.MAX_HARSHNESS_IN_HARDSHIP


def test_hardship_never_harsher_exhaustive_consent_and_value_orderings():
    """Every consent pattern x every ranking of action values."""
    names = list(CATALOGUE)
    for consent in itertools.product([False, True], repeat=len(rules.CHANNELS)):
        flags = rules.CaseFlags(consent=dict(zip(rules.CHANNELS, consent)))
        for order in itertools.permutations(names):
            values = {a: float(i) for i, a in enumerate(order)}
            base = hardship.decide(flags, values, hardship.HardshipSignal())
            for sig in SIGNALS[1:]:
                assert harshness(hardship.decide(flags, values, sig).action) <= harshness(base.action)


def _program(**kw):
    p = {"program_id": "P", "product_types": ["card"], "max_dpd": 90, "min_months_on_book": 6,
         "excludes_insolvency": True, "excludes_broken_plan_90d": True, "min_hardship_level": "possible",
         "effective_from": date(2026, 3, 1), "effective_to": date(9999, 12, 31)}
    return {**p, **kw}


def test_programme_eligibility():
    ctx = hardship.AccountContext(product="card", dpd=30, months_on_book=12)
    on = date(2026, 9, 28)
    assert hardship.eligible_programs([_program()], on, ctx, "possible")
    assert not hardship.eligible_programs([_program()], date(2026, 2, 28), ctx, "possible")  # not yet effective
    assert not hardship.eligible_programs([_program(effective_to=on)], on, ctx, "possible")  # end exclusive
    assert not hardship.eligible_programs([_program(product_types=["mortgage"])], on, ctx, "possible")
    assert not hardship.eligible_programs([_program(max_dpd=29)], on, ctx, "possible")
    assert not hardship.eligible_programs([_program(min_months_on_book=13)], on, ctx, "possible")
    assert not hardship.eligible_programs([_program(min_hardship_level="clear")], on, ctx, "possible")
    insolvent = hardship.AccountContext(product="card", dpd=30, months_on_book=12, insolvency=True)
    assert not hardship.eligible_programs([_program()], on, insolvent, "clear")
    assert hardship.eligible_programs([_program(excludes_insolvency=False)], on, insolvent, "clear")
    broke = hardship.AccountContext(product="card", dpd=30, months_on_book=12, broken_plan_90d=True)
    assert not hardship.eligible_programs([_program()], on, broke, "clear")


# ---- what a flagged case may get (POL-COLL-004) -------------------------------------------------------------

AS_OF = date(2026, 9, 28)
HISTORIES = [
    hardship.HardshipHistory(),  # never offered
    hardship.HardshipHistory(plan_end=date(2026, 10, 3), ever_offered=True),  # plan ending in 5 days
    hardship.HardshipHistory(plan_end=date(2026, 12, 31), ever_offered=True),  # plan running
    hardship.HardshipHistory(ever_offered=True, last_offer_ts=datetime(2026, 9, 20)),
    hardship.HardshipHistory(ever_offered=True, last_offer_ts=datetime(2026, 6, 1)),
]


def test_flagged_cases_never_get_a_payment_reminder_or_collections_call():
    rng = random.Random(7)
    for _ in range(3000):
        flags, values = _random_case(rng)
        for sig in SIGNALS[1:]:
            for hist in HISTORIES:
                d = hardship.decide(flags, values, sig, hist, AS_OF)
                assert d.action in ("wait", hardship.SUPPORT), (sig, hist, d)
                assert d.mandatory_approval == (d.action != "wait")
                assert d.route_to_certified_agent


def test_support_reason():
    hard, vuln = hardship.HardshipSignal("clear"), hardship.HardshipSignal("none", vulnerable=True)
    expect = ["offer_programme", "plan_ending", None, None, "follow_up"]
    assert [hardship.support_reason(hard, h, "wait", AS_OF) for h in HISTORIES] == expect
    assert [hardship.waiting_reason(hard, h, AS_OF) for h in HISTORIES][2:4] == ["plan_running", "recent_offer"]
    # vulnerable only: a certified agent makes the contact the case would have had, and nothing more
    assert hardship.support_reason(vuln, hardship.HardshipHistory(), "agent_call", AS_OF) == "vulnerable_contact"
    assert hardship.support_reason(vuln, hardship.HardshipHistory(), "email_reminder", AS_OF) == "vulnerable_contact"
    assert hardship.support_reason(vuln, hardship.HardshipHistory(), "wait", AS_OF) is None


def test_support_calls_obey_every_contact_rule():
    sig = hardship.HardshipSignal("clear")
    for stop in ("cease_contact", "deceased", "insolvency", "open_dispute", "represented", "experiment_holdout"):
        flags = rules.CaseFlags(consent={c: True for c in rules.CHANNELS}, **{stop: True})
        assert hardship.decide(flags, {}, sig, as_of=AS_OF).action == "wait", stop
    no_phone = rules.CaseFlags(consent={"email": True, "sms": True})
    d = hardship.decide(no_phone, {"email_reminder": 9.0}, sig, as_of=AS_OF)
    assert d.action == "wait" and d.support_reason == "offer_programme"  # due, but blocked: card says why
    full = rules.CaseFlags(consent={"phone": True}, contacts_last_7d=3, contact_cap_7d=3)
    assert hardship.decide(full, {}, sig, as_of=AS_OF).action == "wait"


def test_hardship_level_and_programme_use_limits():
    assert hardship.level_from_case(False, "agent_coded") == "none"
    assert hardship.level_from_case(True, "agent_coded") == "clear"
    assert hardship.level_from_case(True, "customer_form") == "clear"
    assert hardship.level_from_case(True, "model") == "possible"
    ctx = hardship.AccountContext(product="card", dpd=30, months_on_book=12, uses_12m={"P": 1})
    assert not hardship.eligible_programs([_program(max_uses_12m=1)], AS_OF, ctx, "clear")
    assert hardship.eligible_programs([_program(max_uses_12m=2)], AS_OF, ctx, "clear")
    assert hardship.options([_program()], AS_OF, ctx, "none") == []  # vulnerability alone: no programmes
    [opt] = hardship.options([_program(version="2026.1", program_name="Skip", policy_section="§4.2",
                                       approver_level="agent")], AS_OF, ctx, "clear")
    assert (opt.program_id, opt.name, opt.policy_section) == ("P", "Skip", "§4.2")
