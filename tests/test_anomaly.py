"""Anomaly detection on small synthetic series: what is flagged, what is not, and how episodes are explained."""
from datetime import date, timedelta

import pandas as pd

from trustissues.factory.quality import anomaly as A

CFG = {"z_threshold": 3, "counts": {"baseline_weeks": 8, "baseline_loads": 6, "min_baseline": 30, "min_relative_change": 0.3},
       "nulls": {"baseline_weeks": 8, "baseline_loads": 6, "min_rows": 50, "min_change": 0.2}}
START = date(2026, 1, 1)


def daily(values_by_day):
    return pd.DataFrame([{"day": d, "slice": "all", "value": v} for d, v in values_by_day.items()])


def weekly_pattern(days=140, spike=None):
    """Weekdays 1000, Fridays 2000, Sundays 200: normal business rhythm."""
    out = {}
    for i in range(days):
        d = START - timedelta(days=70) + timedelta(days=i)
        out[d] = {4: 2000, 6: 200}.get(d.weekday(), 1000)
    if spike:
        for d, v in spike.items():
            out[d] = v
    return out


def test_busy_fridays_and_quiet_sundays_are_not_anomalies():
    assert A.score_counts(daily(weekly_pattern()), CFG, "daily", START).empty


def test_a_drop_is_flagged_against_the_same_weekday():
    outage = date(2026, 2, 4)  # a Wednesday at 40%
    flags = A.score_counts(daily(weekly_pattern(spike={outage: 400})), CFG, "daily", START)
    assert list(flags["day"]) == [outage] and flags["change"].iloc[0] < -0.5


def test_a_missing_day_counts_as_zero_and_is_flagged():
    data = weekly_pattern(spike={date(2026, 2, 10): 0})
    flags = A.score_counts(daily(data), CFG, "daily", START)
    assert date(2026, 2, 10) in set(flags["day"])


def test_small_slices_are_not_judged():
    data = {START - timedelta(days=70) + timedelta(days=i): 5 for i in range(140)}
    data[date(2026, 2, 4)] = 25  # 5x, but tiny
    assert A.score_counts(daily(data), CFG, "daily", START).empty


def test_a_column_that_suddenly_goes_empty_is_flagged():
    rows = []
    for i in range(140):
        d = START - timedelta(days=70) + timedelta(days=i)
        rows.append({"day": d, "slice": "call/outbound", "column": "channel", "rows": 1000,
                     "nulls": 1000 if d >= date(2026, 2, 10) else 10})
    flags = A.score_nulls(pd.DataFrame(rows), CFG, "daily", START)
    assert flags["day"].min() == date(2026, 2, 10) and set(flags["metric"]) == {"null_rate:channel"}


def ep_row(**k):
    base = {"series": "contacts", "table": "curated.contact_history", "slice": "call/outbound", "metric": "row_count",
            "days": 1, "worst_z": -9.0, "direction": "down", "change": -0.6}
    base.update(k)
    base.setdefault("end", base["start"])
    return base


EVENTS = [{"id": "INC-0923", "start": date(2026, 9, 23), "end": date(2026, 9, 24), "tables": {"contact_history"}, "description": "dialer"},
          {"id": "ECO-0715", "start": date(2026, 7, 15), "end": date(2026, 9, 28), "tables": {"contact_history"}, "description": "layoffs"}]


def test_an_event_explains_its_window_and_a_holiday_is_calendar():
    ep = pd.DataFrame([ep_row(start=date(2026, 9, 24)), ep_row(start=date(2026, 5, 18)), ep_row(start=date(2026, 3, 3))])
    out = A.explain(ep, EVENTS, {date(2026, 5, 18)}, lag=1)
    assert list(out["status"]) == ["explained", "calendar", "unexplained"]
    assert out["explained_by"].iloc[0].startswith("INC-0923")


def test_a_long_event_does_not_explain_a_column_going_empty():
    ep = pd.DataFrame([ep_row(start=date(2026, 8, 20), metric="null_rate:channel", worst_z=40.0, direction="up")])
    assert A.explain(ep, EVENTS, set(), lag=1)["status"].iloc[0] == "unexplained"


def test_the_same_change_every_month_is_recurring():
    ep = pd.DataFrame([ep_row(start=date(2026, m, 21), direction="up", worst_z=30.0, change=3.5, slice="card",
                              series="cases_opened", table="curated.collections_cases") for m in (2, 3, 4, 5, 6)])
    out = A.explain(ep, [], set(), lag=1)
    assert set(out["status"]) == {"recurring"}


def test_a_dip_next_to_a_rise_is_two_episodes():
    days = pd.DataFrame([
        {"day": date(2026, 5, 17), "z": 6.0, "change": 0.5}, {"day": date(2026, 5, 18), "z": -9.7, "change": -1.0},
        {"day": date(2026, 5, 19), "z": 3.4, "change": 0.4}]).assign(
        slice="call/outbound", metric="row_count", series="contacts", table="curated.contact_history", mode="daily",
        value=0, baseline=0)
    ep = A.episodes(days, gap_days=3)
    assert sorted(ep["direction"]) == ["down", "up"] and ep.loc[ep["direction"] == "up", "days"].iloc[0] == 2
