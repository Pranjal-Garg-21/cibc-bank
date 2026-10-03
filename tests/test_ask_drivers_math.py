import pytest

from trustissues.ask import drivers as D


def test_rate_decomposition_sums_exactly_to_the_change():
    before = {"card": (10.0, 100), "auto": (20.0, 50), "loc": (30.0, 50)}
    after = {"card": (15.0, 80), "auto": (20.0, 100), "loc": (10.0, 20)}
    r0, r1, segs = D.decompose_rates(before, after, "product")
    assert r0 == pytest.approx((1000 + 1000 + 1500) / 200)
    assert sum(s.contribution for s in segs) == pytest.approx(r1 - r0)
    assert all(s.rate_effect + s.mix_effect == pytest.approx(s.contribution) for s in segs)
    assert sum(s.share_of_change for s in segs) == pytest.approx(1.0)
    assert [s.value for s in segs][0] in {"card", "loc", "auto"}


def test_same_rates_different_mix_is_all_mix_effect():
    before = {"a": (10.0, 50), "b": (30.0, 50)}
    after = {"a": (10.0, 20), "b": (30.0, 80)}
    r0, r1, segs = D.decompose_rates(before, after, "x")
    assert r1 > r0 and all(s.rate_effect == 0 for s in segs)
    assert sum(s.mix_effect for s in segs) == pytest.approx(r1 - r0)


def test_segment_present_in_one_period_only():
    before = {"a": (10.0, 100)}
    after = {"a": (10.0, 50), "new": (50.0, 50)}
    r0, r1, segs = D.decompose_rates(before, after, "x")
    new = next(s for s in segs if s.value == "new")
    assert new.before is None and new.rate_effect == 0 and new.contribution == pytest.approx(25.0)
    assert sum(s.contribution for s in segs) == pytest.approx(r1 - r0)


def test_empty_period_is_an_error():
    with pytest.raises(ValueError):
        D.decompose_rates({}, {"a": (1.0, 1)}, "x")


def test_no_change_gives_no_share():
    _, _, segs = D.decompose_rates({"a": (5.0, 10)}, {"a": (5.0, 10)}, "x")
    assert segs[0].share_of_change is None


def test_count_decomposition():
    r0, r1, segs = D.decompose_counts({"site_a": 100, "site_b": 100}, {"site_a": 20, "site_b": 90, "site_c": 5}, "agent_site")
    assert (r0, r1) == (200.0, 115.0)
    assert segs[0].value == "site_a" and segs[0].contribution == -80.0
    assert sum(s.contribution for s in segs) == r1 - r0
    assert D.concentration(segs) == pytest.approx(80 / 95)


def test_rank_across_dimensions_and_limit():
    a = D.decompose_rates({"x": (1.0, 10), "y": (5.0, 10)}, {"x": (2.0, 10), "y": (5.0, 10)}, "d1")[2]
    b = D.decompose_rates({"p": (1.0, 10)}, {"p": (9.0, 10)}, "d2")[2]
    top = D.rank_segments({"d1": a, "d2": b}, limit=2)
    assert len(top) == 2 and abs(top[0].contribution) >= abs(top[1].contribution)


@pytest.mark.parametrize("dim,ok", [("product", True), ("agent_site", True), ("prior_broken_ptp_count", True),
                                    ("gender_code", False), ("citizenship_status", False), ("full_name", False),
                                    ("test_cell", False), ("postal_code", False)])
def test_only_approved_dimensions(dim, ok):
    assert D.approved_dimension(dim) is ok


def test_comparison_period_whole_month_uses_previous_calendar_month():
    from datetime import date
    from trustissues.ask.asof import Period
    prev = D.comparison_period(Period(date(2026, 9, 1), date(2026, 10, 1), "september 2026"), None)
    assert (prev.start, prev.end) == (date(2026, 8, 1), date(2026, 9, 1))
    jan = D.comparison_period(Period(date(2026, 1, 1), date(2026, 2, 1), "january 2026"), None)
    assert (jan.start, jan.end) == (date(2025, 12, 1), date(2026, 1, 1))
    two_days = D.comparison_period(Period(date(2026, 9, 23), date(2026, 9, 25), "23-24"), None)
    assert (two_days.start, two_days.end) == (date(2026, 9, 21), date(2026, 9, 23))
    named = Period(date(2026, 7, 1), date(2026, 8, 1), "july")
    assert D.comparison_period(Period(date(2026, 9, 1), date(2026, 10, 1), "s"), named) is named


def test_broad_based_move_is_recognised_and_a_local_one_is_not():
    even = {"site": D.decompose_counts({"a": 100, "b": 100, "c": 100, "d": 100}, {"a": 40, "b": 42, "c": 38, "d": 41}, "site")[2]}
    assert "Broad-based" in D.broad_based(even, -0.6)
    local = {"site": D.decompose_counts({"a": 100, "b": 100, "c": 100, "d": 100}, {"a": 100, "b": 100, "c": 100, "d": 0}, "site")[2]}
    assert D.broad_based(local, -0.25) is None
