"""Text controls: the scrubber removes identifiers and leaves business content alone; the views refuse what they must."""
from datetime import date

import duckdb
import pytest

from trustissues.factory.curate.text_controls import (build_views, eligible_notes, luhn_ok, scrub, summary)


# ---- scrubber ------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("call back 416-555-0123 tonight", "call back [PHONE] tonight"),
    ("call (416) 555 0123", "call [PHONE]"),
    ("tel +1 604.555.0199 ok", "tel [PHONE] ok"),
    ("mail jane.doe+x@mail.example.ca pls", "mail [EMAIL] pls"),
    ("card 4111 1111 1111 1111 on file", "card [CARD] on file"),
    ("card 4111111111111111", "card [CARD]"),
    ("SIN 046-454-286", "SIN [ID]"),
    ("postal M5V 2T6 confirmed", "postal [POSTAL] confirmed"),
    ("acct 123456789012 closed", "acct [NUMBER] closed"),
])
def test_identifiers_are_replaced(text, expected):
    assert scrub(text).text == expected


@pytest.mark.parametrize("text", [
    "cust called in, PTP $180 Jul 31, snt pmt link",  # real note shape
    "promise 1500.00 on 2026-07-31",
    "paid $1,234,567 today",
    "HP-LOAN-RED3 offered; see POL-COLL-004 §4.2",
    "client a appelé; paiera après la paie; promesse 80 $ 7 août",
    "ref 20260731 and 3 months at 50%",
    "spoke w/ cust",
])
def test_business_content_is_left_alone(text):
    assert scrub(text).text == text and scrub(text).found == {}


def test_card_number_failing_luhn_is_still_removed():
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")
    assert scrub("card 4111111111111112").text == "card [NUMBER]"  # not a valid card, still removed


@pytest.mark.parametrize("text", [  # shapes taken from the real notes: odd groupings that fail the Luhn check
    "SPOKE TO CUSTOMER, CARD 450 843 2129 9978",
    "vulnerable; card 4520 170 246 2979; refer",
    "spoke w/ cust; card 450 4790 3718 3622",
])
def test_oddly_grouped_card_numbers_leave_no_digits_behind(text):
    out = scrub(text).text
    assert not any(ch.isdigit() for ch in out), out


def test_counts_and_empty_input():
    s = scrub("a@b.co or c@d.co, tel 416-555-0123")
    assert s.found == {"EMAIL": 2, "PHONE": 1}
    assert scrub(None).text == "" and scrub("").text == ""


# ---- eligibility views -----------------------------------------------------------------------------
AS_OF = date(2026, 9, 28)


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE SCHEMA curated")
    c.execute("""CREATE TABLE curated.agent_notes (note_id VARCHAR, crm_customer_id VARCHAR, note_ts TIMESTAMP,
        note_type VARCHAR, note_language VARCHAR, note_text VARCHAR, redaction_status VARCHAR, retention_until DATE)""")
    c.execute("""INSERT INTO curated.agent_notes VALUES
      ('n_ok_auto','C1','2026-05-01 10:00','call_note','EN','cust paid 555-123-4567','auto_redacted','2030-01-01'),
      ('n_ok_none','C1','2026-05-02 10:00','call_note','EN','promised $180','none','2030-01-01'),
      ('n_review','C1','2026-05-03 10:00','call_note','EN','text','needs_review','2030-01-01'),
      ('n_expired','C1','2026-05-04 10:00','call_note','EN','text','none','2026-09-27'),
      ('n_today','C1','2026-05-05 10:00','call_note','EN','text','none','2026-09-28'),
      ('n_future','C1','2026-10-01 10:00','call_note','EN','text','none','2030-01-01'),
      ('n_empty','C1','2026-05-06 10:00','call_note','EN','  ','none','2030-01-01'),
      ('n_noret','C1','2026-05-07 10:00','call_note','EN','text','none',NULL),
      ('n_nostatus','C1','2026-05-08 10:00','call_note','EN','text',NULL,'2030-01-01')""")
    c.execute("""CREATE TABLE curated.call_transcripts (transcript_id VARCHAR, contact_id VARCHAR, crm_customer_id VARCHAR,
        call_start_ts TIMESTAMP, pii_redaction_applied BOOLEAN, pci_redaction_applied BOOLEAN)""")
    c.execute("CREATE TABLE curated.contact_history (contact_id VARCHAR, recording_retention_until DATE)")
    c.execute("""INSERT INTO curated.contact_history VALUES ('k1','2030-01-01'),('k2','2026-01-01'),('k3',NULL),
                 ('k4','2030-01-01'),('k5','2030-01-01')""")
    c.execute("""INSERT INTO curated.call_transcripts VALUES
      ('t_ok','k1','C1','2026-05-01 09:00',true,true),
      ('t_expired','k2','C1','2026-05-01 09:00',true,true),
      ('t_noret','k3','C1','2026-05-01 09:00',true,true),
      ('t_pii','k4','C1','2026-05-01 09:00',false,true),
      ('t_pci','k5','C1','2026-05-01 09:00',true,false),
      ('t_nocontact','kX','C1','2026-05-01 09:00',true,true)""")
    build_views(c, AS_OF, statuses=["auto_redacted", "none"])
    return c


def reasons(c, view, key):
    return dict(c.execute(f"SELECT {key}, reason FROM curated.{view}").fetchall())


def test_each_note_rule_refuses_what_it_should(con):
    r = reasons(con, "note_text_eligibility", "note_id")
    assert r == {"n_ok_auto": "ok", "n_ok_none": "ok", "n_review": "redaction_status:needs_review",
                 "n_expired": "past_retention", "n_today": "ok",  # retention ending today is still in date
                 "n_future": "after_as_of_date", "n_empty": "empty_text", "n_noret": "no_retention_date",
                 "n_nostatus": "redaction_status:null"}


def test_strict_setting_excludes_none(con):
    build_views(con, AS_OF, statuses=["auto_redacted"])
    ok = {k for k, v in reasons(con, "note_text_eligibility", "note_id").items() if v == "ok"}
    assert ok == {"n_ok_auto"}


def test_each_transcript_rule_refuses_what_it_should(con):
    assert reasons(con, "transcript_text_eligibility", "transcript_id") == {
        "t_ok": "ok", "t_expired": "past_retention", "t_noret": "no_retention_date", "t_pii": "pii_not_redacted",
        "t_pci": "pci_not_redacted", "t_nocontact": "no_retention_date"}  # no contact row: unknown retention, so refused


def test_eligible_notes_are_scrubbed_and_ineligible_ones_never_appear(con):
    got = {n["note_id"]: n for n in eligible_notes(con, scrub_text=True)}
    assert set(got) == {"n_ok_auto", "n_ok_none", "n_today"}
    assert got["n_ok_auto"]["note_text"] == "cust paid [PHONE]" and got["n_ok_auto"]["scrubbed"] == {"PHONE": 1}
    assert got["n_ok_none"]["note_text"] == "promised $180"


def test_summary_counts_every_row(con):
    s = summary(con)
    assert sum(n for _, n in s["notes"]) == 9 and sum(n for _, n in s["transcripts"]) == 6
