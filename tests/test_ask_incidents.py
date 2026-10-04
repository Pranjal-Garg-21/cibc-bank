"""ask/incidents.py: recorded incidents that overlap the period a "why did X change" answer explains."""
from datetime import date

from trustissues.ask import incidents

DIALER = """> Synthetic document created for the Maple Bank collections hackathon.

# Incident Report IR-2026-0923

**When**: 23-24 September 2026. The predictive dialer was degraded; outbound call capacity fell about 60%.
Expect fewer call attempts and right-party contacts on those days; this is not a change in customer behaviour."""
PAYMENTS = """# Incident Report IR-2026-0914

**When**: 14 September 2026, 09:10-15:40 ET. **Severity**: sev2.

Payments made in the mobile app and online banking were not posted until the next day."""


def _pool():
    return (incidents.parse("IR-2026-0923", "Incident Report: Outbound Dialer Outage", DIALER, "2026-09-25"),
            incidents.parse("IR-2026-0914", "Incident Report: Payment Posting Delay", PAYMENTS, "2026-09-16"))


def test_dates_and_text_are_read_from_the_report():
    dialer, payments = _pool()
    assert (dialer.start, dialer.end) == (date(2026, 9, 23), date(2026, 9, 25))
    assert (payments.start, payments.end) == (date(2026, 9, 14), date(2026, 9, 15))
    assert dialer.summary.startswith("The predictive dialer was degraded") and "Synthetic" not in dialer.summary
    assert "sev2" not in payments.summary and payments.summary.startswith("Payments made")


def test_only_incidents_overlapping_the_period_and_known_by_the_as_of_date():
    pool = _pool()
    hit = incidents.overlapping(date(2026, 9, 23), date(2026, 9, 25), date(2026, 9, 28), pool)
    assert [i.doc_id for i in hit] == ["IR-2026-0923"]
    assert incidents.overlapping(date(2026, 8, 1), date(2026, 9, 1), date(2026, 9, 28), pool) == []
    # the dialer report was published on 25 Sep: an answer as of 24 Sep cannot know it
    assert incidents.overlapping(date(2026, 9, 23), date(2026, 9, 25), date(2026, 9, 24), pool) == []


def test_sentence_cites_the_report():
    s = incidents.sentence(_pool()[0])
    assert s.startswith("Recorded incident IR-2026-0923 (Outbound Dialer Outage, 23-24 September 2026): The predictive")
