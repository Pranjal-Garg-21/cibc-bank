"""nba/text_signals.py: P4's text features as hardship / vulnerability signals and decision-card evidence."""
import json
from datetime import date

import duckdb
import pytest

from trustissues.common.interfaces import apply_stubs
from trustissues.nba import hardship, rules, text_signals
from trustissues.nba.queue import case_input, decide_case
from test_nba_decisions import ALL_CONSENT, CTX, _case

AS_OF = date(2026, 9, 28)


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    apply_stubs(c)  # gold.text_features with the I3 columns only
    c.execute("ALTER TABLE gold.text_features ADD COLUMN vulnerability BOOLEAN")  # P4's extra (text_extract.ensure_table)
    rows = [  # person, id, ts, hardship, delay, ptp, dispute, evidence, confidence, vulnerability
        ("G1", "N1", "2026-09-20 10:00", True, "reduced_income", "none", False, "lost my job last month", 0.9, False),
        ("G1", "N2", "2026-09-25 10:00", False, None, "strong", False, "can pay $95 Oct 14", 0.85, False),
        ("G1", "N3", "2026-09-26 10:00", False, "dispute", "none", True, "late fee was wrong", 0.8, False),
        ("G1", "N4", "2026-09-27 10:00", False, None, "weak", False, "will try", 0.7, False),
        ("G2", "N5", "2026-09-28 09:00", True, None, "none", False, "hours cut", 0.9, False),     # on the as-of day
        ("G3", "N6", "2026-05-01 10:00", True, None, "none", False, "lost my job", 0.9, False),   # too old
        ("G4", "N7", "2026-09-01 10:00", True, None, "none", False, None, 0.5, False),            # quote not verbatim
        ("G5", "N8", "2026-09-01 10:00", True, None, "none", False, "money is tight", 0.4, False),  # low confidence
        ("G6", "T1", "2026-09-10 10:00", False, None, "none", False, "my mother just died", 0.8, True),
    ]
    c.executemany("""INSERT INTO gold.text_features (golden_customer_id, source_id, source_ts, hardship_signal,
        stated_delay_reason, ptp_intent_strength, dispute_mentioned, evidence_sentence, confidence, vulnerability,
        prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'text_features_v1')""", rows)
    return c


def test_only_recent_confident_quoted_text_before_the_as_of_date_counts(con):
    got = text_signals.load(con, ["G1", "G2", "G3", "G4", "G5", "G6", "G9"], AS_OF)
    assert set(got) == {"G1", "G6"}
    assert got["G1"].hardship and not got["G1"].vulnerable
    assert got["G6"].vulnerable and not got["G6"].hardship


def test_evidence_puts_what_flagged_the_case_first_then_disputes_then_promises(con):
    quotes = text_signals.load(con, ["G1"], AS_OF)["G1"].evidence
    assert [q.source_id for q in quotes] == ["N1", "N3", "N2"]  # at most MAX_QUOTES
    assert quotes[0].quote == "lost my job last month" and quotes[0].source_ts.startswith("2026-09-20")


def test_no_table_columns_or_people_means_no_signal():
    c = duckdb.connect(":memory:")
    c.execute("CREATE SCHEMA gold")
    assert text_signals.load(c, ["G1"], AS_OF) == {}
    apply_stubs(c)  # empty stub table, no vulnerability column: still works
    assert text_signals.load(c, ["G1"], AS_OF) == {}
    assert text_signals.load(c, [], AS_OF) == {}


def _row(**kw):
    return {"case_id": "CS-1", "golden_customer_id": "G1", "crm_customer_id": "C1", "assigned_agent_id": "AG-1",
            "assigned_team": "T1", "agent_certified": False, "total_overdue": 500.0, "primary_product": "card",
            "current_dpd": 40, "months_on_book": 24, "broken_plan_90d": False, "hardship_flag": False,
            "hardship_source": None, "vulnerable": False, "plan_end": None, "ever_offered": False,
            "last_offer_ts": None, **kw}


def _input(text=None, **kw):
    return case_input(_row(**kw), rules.CaseFlags(consent=dict(ALL_CONSENT)), [], {}, AS_OF, text=text, risk=0.8)


def test_text_raises_hardship_to_possible_at_most_and_never_lowers_a_recorded_level(con):
    sig = text_signals.load(con, ["G1"], AS_OF)["G1"]
    c = _input(sig)
    assert c.signal.level == "possible" and c.text_flagged and len(c.evidence) == 3
    assert _input(sig, hardship_flag=True, hardship_source="agent_coded").signal.level == "clear"  # kept
    assert not _input(sig, hardship_flag=True, hardship_source="agent_coded").text_flagged
    assert _input(None).signal == hardship.HardshipSignal()  # no text: unchanged


def test_text_vulnerability_routes_to_a_certified_agent(con):
    c = _input(text_signals.load(con, ["G6"], AS_OF)["G6"], golden_customer_id="G6")
    assert c.signal.vulnerable and c.text_flagged


def test_a_text_flagged_case_gets_no_reminder_and_its_card_quotes_the_evidence(con):
    sig = text_signals.load(con, ["G1"], AS_OF)["G1"]
    plain = decide_case(_case(), CTX)
    flagged = decide_case(_case(signal=hardship.HardshipSignal("possible"), evidence=sig.evidence, text_flagged=True),
                          CTX)
    assert plain["action"] not in ("wait", "support_call")
    assert flagged["action"] in ("wait", "support_call")
    card = json.loads(flagged["decision_card"])
    assert [e["source_id"] for e in card["evidence"]] == ["N1", "N3", "N2"]
    assert "notes or calls" in card["summary"]
