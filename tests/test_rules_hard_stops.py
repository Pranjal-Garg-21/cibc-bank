"""nba/rules.py: no contact action survives cease, deceased, insolvency or an open dispute."""
import itertools
from datetime import time

import pytest

from trustissues.nba import rules
from trustissues.nba.actions import CATALOGUE

ALL_CONSENT = {c: True for c in rules.CHANNELS}
CONTACT_ACTIONS = [a for a, spec in CATALOGUE.items() if spec.contact]


def _flags(**kw):
    return rules.CaseFlags(consent=dict(ALL_CONSENT), **kw)


def test_clean_case_allows_every_action():
    r = rules.allowed_actions(_flags())
    assert set(r.allowed) == set(CATALOGUE) and not r.blocked


@pytest.mark.parametrize("stop", rules.HARD_STOPS)
def test_each_hard_stop_blocks_all_contact(stop):
    field = {"cease_contact": "cease_contact", "deceased": "deceased", "insolvency": "insolvency",
             "open_dispute": "open_dispute"}[stop]
    r = rules.allowed_actions(_flags(**{field: True}))
    assert r.allowed == ["wait"]
    assert all(r.blocked[a] == stop for a in CONTACT_ACTIONS)


def test_hard_stops_win_in_every_combination():
    """Every combination of hard stops and the softer flags: only `wait` survives, blocked by a hard stop."""
    names = ["cease_contact", "deceased", "insolvency", "open_dispute"]
    soft = ["represented", "do_not_call"]
    for stops in itertools.product([False, True], repeat=len(names)):
        if not any(stops):
            continue
        for softs in itertools.product([False, True], repeat=len(soft)):
            for consent in ({}, ALL_CONSENT):
                f = rules.CaseFlags(consent=dict(consent), contacts_last_7d=5,
                                    **dict(zip(names, stops)), **dict(zip(soft, softs)))
                r = rules.allowed_actions(f)
                assert r.allowed == ["wait"]
                assert all(r.blocked[a] in rules.HARD_STOPS for a in CONTACT_ACTIONS)


def test_wait_is_never_blocked():
    r = rules.allowed_actions(rules.CaseFlags(cease_contact=True, deceased=True))
    assert "wait" in r.allowed and "wait" not in r.blocked


def test_consent_is_default_deny_and_per_channel():
    r = rules.allowed_actions(rules.CaseFlags(consent={"email": True}))
    assert r.allowed == ["wait", "email_reminder"]
    assert r.blocked["agent_call"] == "consent"


def test_contact_cap_and_do_not_call():
    assert rules.allowed_actions(_flags(contacts_last_7d=3, contact_cap_7d=3)).allowed == ["wait"]
    r = rules.allowed_actions(_flags(do_not_call=True))
    assert r.blocked == {"agent_call": "do_not_call", "support_call": "do_not_call"}  # both are phone calls


def test_contact_hours_only_bind_real_time_channels():
    r = rules.allowed_actions(_flags(planned_local_time=time(22, 0), permitted_call_hours=(time(8), time(21)),
                                     preferred_window=(time(9), time(18))))
    assert r.blocked == {"agent_call": "outside_permitted_hours", "support_call": "outside_permitted_hours", "sms_reminder": "outside_permitted_hours"}
    r = rules.allowed_actions(_flags(planned_local_time=time(19, 0), permitted_call_hours=(time(8), time(21)),
                                     preferred_window=(time(9), time(18))))
    assert r.blocked == {"agent_call": "outside_preferred_window", "support_call": "outside_preferred_window", "sms_reminder": "outside_preferred_window"}


def test_only_the_internal_do_not_call_list_blocks_calls():
    """POL-COLL-001 §5.3: the National DNCL covers telemarketing only and does not block collection calls."""
    import duckdb

    from trustissues.features import sources

    con = duckdb.connect()
    sources.create_empty_tables(con)  # every source, all VARCHAR, as load_raw makes them
    con.execute('ALTER TABLE gold.customer_safeguards ADD COLUMN national_dncl VARCHAR')  # in gold, never read by P3
    for case, cust, national, internal in (("CS-N", "C-N", "true", "false"), ("CS-I", "C-I", "false", "true")):
        con.execute(f"INSERT INTO {sources.SCHEMA}.collections_cases (case_id, coll_customer_ref) VALUES (?, ?)",
                    [case, cust])
        con.execute("INSERT INTO gold.customer_xref (golden_customer_id, source_system, source_key) VALUES (?, 'crm', ?)",
                    [f"G-{cust}", cust])
        con.execute("""INSERT INTO gold.customer_safeguards (golden_customer_id, consent_phone, national_dncl,
                       internal_do_not_call) VALUES (?, 'true', ?, ?)""", [f"G-{cust}", national, internal])
    flags = rules.load_case_flags(con)
    assert not flags["CS-N"].do_not_call and "agent_call" in rules.allowed_actions(flags["CS-N"]).allowed
    assert flags["CS-I"].do_not_call and rules.allowed_actions(flags["CS-I"]).blocked["agent_call"] == "do_not_call"


def test_contact_window_for_the_day():
    """POL-COLL-001 §5.1: 07:00-21:00 local; Sundays 13:00-17:00 only where Sunday contact is permitted; narrowed to
    the preferred window when they overlap, never widened by it."""
    from datetime import date

    monday, sunday = date(2026, 9, 28), date(2026, 9, 27)
    assert rules.contact_window(_flags(contact_day=monday)) == (time(7), time(21))
    assert rules.contact_window(_flags(contact_day=monday, preferred_window=(time(9), time(18)))) == (time(9), time(18))
    assert rules.contact_window(_flags(contact_day=monday, preferred_window=(time(5), time(6)))) == (time(7), time(21))
    assert rules.contact_window(_flags(contact_day=sunday, sunday_contact=True)) == (time(13), time(17))
    assert rules.contact_window(_flags(contact_day=sunday, sunday_contact=False)) is None
    r = rules.allowed_actions(_flags(contact_day=sunday, sunday_contact=False))
    assert r.blocked == {"agent_call": "no_sunday_contact", "support_call": "no_sunday_contact",
                         "sms_reminder": "no_sunday_contact"}  # email, push and letters may still go
    assert not rules.allowed_actions(_flags(contact_day=sunday, sunday_contact=True)).blocked


def test_no_collection_contact_on_a_statutory_holiday():
    from datetime import date

    r = rules.allowed_actions(_flags(contact_day=date(2026, 9, 30)))  # National Day for Truth and Reconciliation
    assert set(r.allowed) == {"wait", "letter"}  # a letter may still be posted
    assert set(r.blocked.values()) == {"statutory_holiday"}
