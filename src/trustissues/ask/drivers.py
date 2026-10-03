"""Driver decomposition: where did a metric move between two periods?

For a metric that is a weighted average (a rate, or a per-unit average), the overall value is
R = sum over segments of w_v * r_v, with w_v the segment's share of the denominator and r_v its value.
The change R1 - R0 then splits EXACTLY, segment by segment, as
    w1*r1 - w0*r0 = wbar * (r1 - r0)  +  rbar * (w1 - w0)
                     rate effect          mix effect
(bars are midpoints). Counts split as n1 - n0. The split is descriptive: it says where the change is,
not why it happened, and the answer says so.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, timedelta

from trustissues.governance import masking

from .asof import Period
from .data.execute import execute
from .data.metric_templates import DIMENSIONS, FAMILIES, render_metric
from .data.validator import QueryPolicy, validate

# Approved split dimensions (the plan's whitelist plus operational fields). Never a protected attribute.
EXCLUDED = {"test_cell"}  # an experiment label, not an operational driver
MAX_SEGMENTS = 10
SMALL_CHANGE = 0.02  # below 2% relative change the shares are noise
BROAD_SPREAD = 0.15  # volume segments within 15 points of the overall % change = a broad-based move


@dataclass(frozen=True)
class Segment:
    dimension: str
    value: str
    contribution: float  # share of the total change attributable to this segment (same unit as the metric)
    rate_effect: float
    mix_effect: float
    share_of_change: float | None  # contribution / total change; None when the total is ~0
    before: float | None  # rate (or count) in the earlier period; None if the segment was absent
    after: float | None
    n_before: int
    n_after: int


@dataclass
class DriverReport:
    metric: str
    unit_note: str
    total_before: float
    total_after: float
    segments: list[Segment]
    notes: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)  # every statement executed, with its bound parameters

    @property
    def change(self) -> float:
        return self.total_after - self.total_before


def approved_dimension(dimension: str) -> bool:
    """A split dimension must not be protected or direct PII, and not an experiment label."""
    return dimension not in EXCLUDED and masking.classify(dimension) == "none"


def decompose_rates(before: dict, after: dict, dimension: str) -> tuple[float, float, list[Segment]]:
    """before/after: {segment value: (rate, n)}. -> (overall before, overall after, segments)."""
    n0, n1 = sum(n for _, n in before.values()), sum(n for _, n in after.values())
    if n0 == 0 or n1 == 0:
        raise ValueError("A period with no observations cannot be decomposed")
    r0 = sum(r * n for r, n in before.values()) / n0
    r1 = sum(r * n for r, n in after.values()) / n1
    change = r1 - r0
    out = []
    for v in set(before) | set(after):
        ra, na = before.get(v, (None, 0))
        rb, nb = after.get(v, (None, 0))
        w0, w1 = na / n0, nb / n1
        if ra is None or rb is None:  # present in one period only: the whole effect is mix
            contribution = (w1 * rb if rb is not None else 0.0) - (w0 * ra if ra is not None else 0.0)
            rate_eff, mix_eff = 0.0, contribution
        else:
            rate_eff = (w0 + w1) / 2 * (rb - ra)
            mix_eff = (ra + rb) / 2 * (w1 - w0)
            contribution = rate_eff + mix_eff
        out.append(Segment(dimension, str(v), contribution, rate_eff, mix_eff,
                           contribution / change if abs(change) > 1e-12 else None, ra, rb, na, nb))
    return r0, r1, sorted(out, key=lambda s: -abs(s.contribution))


def decompose_counts(before: dict, after: dict, dimension: str) -> tuple[float, float, list[Segment]]:
    """before/after: {segment value: count}. Contribution = change in the segment's count."""
    t0, t1 = sum(before.values()), sum(after.values())
    change = t1 - t0
    out = []
    for v in set(before) | set(after):
        a, b = before.get(v, 0), after.get(v, 0)
        out.append(Segment(dimension, str(v), float(b - a), 0.0, float(b - a),
                           (b - a) / change if change else None, float(a), float(b), a, b))
    return float(t0), float(t1), sorted(out, key=lambda s: -abs(s.contribution))


def rank_segments(per_dimension: dict[str, list[Segment]], limit: int = MAX_SEGMENTS) -> list[Segment]:
    """Largest absolute contributions across all dimensions. Each dimension explains the same total change,
    so the segments are alternative views of it, not additive across dimensions."""
    flat = [s for segs in per_dimension.values() for s in segs]
    return sorted(flat, key=lambda s: -abs(s.contribution))[:limit]


def concentration(segments: list[Segment]) -> float:
    """Share of the absolute movement in the single largest segment (1.0 = one segment explains it all)."""
    total = sum(abs(s.contribution) for s in segments)
    return abs(segments[0].contribution) / total if total else 0.0


# ---- running the decomposition ------------------------------------------------------------------------------

# Plain outbound-call volume is not a certified metric: it has its own structural query over the contacts view.
VOLUME_DIMENSIONS = {"agent_site": "agent_site", "contact_hour_local": "contact_hour_local", "province": "province"}
VOLUME_SQL = ("SELECT {expr} AS dim_value, count(*) AS n FROM gold.semantic_contacts "
              "WHERE direction='outbound' AND channel='call' AND contact_ts_local >= $start AND contact_ts_local < $end "
              "GROUP BY 1")


class DriversUnsupported(ValueError):
    pass


def comparison_period(current: Period, explicit: Period | None) -> Period:
    """The period to compare against: the one named in the question, else the same-length window just before."""
    if explicit is not None:
        return explicit
    if current.start.day == 1 and (current.end - timedelta(days=1)).day >= 28 and current.end.day == 1:
        prev_end = current.start  # a whole calendar month: compare with the month before
        prev_start = date(prev_end.year - (prev_end.month == 1), (prev_end.month - 2) % 12 + 1, 1)
        return Period(prev_start, prev_end, f"the previous month ({prev_start:%B %Y})")
    days = (current.end - current.start).days
    return Period(current.start - timedelta(days=days), current.start, f"the {days} days before")


def candidate_dimensions(metric_id: str) -> list[str]:
    allowed = DIMENSIONS[FAMILIES[metric_id]]
    return [d for d in allowed if approved_dimension(d)]


def format_params(params: dict) -> str:
    """Readable, ISO-dated parameter list for the "show the working" record."""
    return "{" + ", ".join(f"{k}: {v.isoformat() if hasattr(v, 'isoformat') else repr(v)}" for k, v in params.items()) + "}"


def _run(sql, params, policy, con, database, log=None):
    checked = validate(sql, policy)
    if log is not None:
        log.append(f"{checked.sql}\n-- parameters: {format_params(params)}")
    return execute(checked, params, con=con, database=database)


def _with_period(parameters: dict, period: Period) -> dict:
    if "start" not in parameters or "end" not in parameters:
        raise DriversUnsupported("This metric is a point-in-time value; it cannot be compared across two periods.")
    return {**parameters, "start": period.start, "end": period.end}


def explain_metric(metric_id: str, parameters: dict, current: Period, previous: Period, policy: QueryPolicy, *,
                   con=None, database=None, variant: str = "default", cap_at: int | None = None) -> DriverReport:
    if metric_id == "contact_volume":
        return _explain_volume(current, previous, policy, con, database)
    dims = candidate_dimensions(metric_id)
    if not dims:
        raise DriversUnsupported("No approved split dimension exists for this metric.")
    per_dim: dict[str, list[Segment]] = {}
    totals = None
    log: list[str] = []
    for dim in dims:
        sides = []
        for period in (previous, current):
            q = render_metric(metric_id, parameters=_with_period(parameters, period), dimensions=(dim,),
                              variant=variant, cap_at=cap_at if dim == "prior_broken_ptp_count" else None)
            res = _run(q.sql, q.parameters, policy, con, database, log)
            cols = list(res.columns)
            if "metric_value" not in cols or "denominator" not in cols:
                raise DriversUnsupported("This metric has no per-segment denominator, so a change cannot be split by rate and mix.")
            iv, idn = cols.index("metric_value"), cols.index("denominator")
            sides.append({str(r[0]): (float(r[iv]), int(r[idn])) for r in res.rows if r[iv] is not None and r[idn]})
        try:
            r0, r1, segs = decompose_rates(sides[0], sides[1], dim)
        except ValueError as e:
            raise DriversUnsupported(str(e)) from e
        totals = (r0, r1)
        if len(segs) > 1:  # a one-value dimension (e.g. channel = call only) explains nothing
            per_dim[dim] = segs
    if not per_dim:
        raise DriversUnsupported("No approved dimension has more than one segment for this metric.")
    notes = ["Splits are alternative views of the same change, not additive across dimensions.",
             "Descriptive only: this shows where the change occurred, not why."]
    if abs(totals[1] - totals[0]) < SMALL_CHANGE * max(abs(totals[0]), abs(totals[1]), 1e-12):
        notes.append(f"The overall change is small ({totals[1] - totals[0]:+.4g}); the segment movements below "
                     "largely offset each other, so shares of the change are not shown.")
        for dim, segs in per_dim.items():
            per_dim[dim] = [dataclasses.replace(x, share_of_change=None) for x in segs]
    best = max(per_dim, key=lambda d: concentration(per_dim[d]))
    notes.append(f"Most concentrated split: {best} (largest segment holds {concentration(per_dim[best]):.0%} of the movement).")
    return DriverReport(metric_id, "same unit as the metric", totals[0], totals[1], rank_segments(per_dim), notes, log)


def _explain_volume(current: Period, previous: Period, policy, con, database) -> DriverReport:
    days_now, days_before = (current.end - current.start).days, (previous.end - previous.start).days
    per_dim: dict[str, list[Segment]] = {}
    totals = None
    log: list[str] = []
    for dim, expr in VOLUME_DIMENSIONS.items():
        sides = []
        for period in (previous, current):
            res = _run(VOLUME_SQL.format(expr=expr), {"start": period.start, "end": period.end}, policy, con, database, log)
            scale = 1.0 if days_now == days_before else 1.0 / max((period.end - period.start).days, 1)
            sides.append({str(r[0]): r[1] * scale for r in res.rows if r[0] is not None})
        r0, r1, segs = decompose_counts(sides[0], sides[1], dim)
        per_dim[dim] = segs
        totals = (r0, r1)
    overall = (totals[1] - totals[0]) / totals[0] if totals and totals[0] else None
    broad = broad_based(per_dim, overall)
    notes = ["Outbound calls only (channel 'call', direction 'outbound').",
             "Descriptive only: this shows where the volume changed. The cause needs incident and operations records, "
             "which are not connected, so no cause is claimed."]
    if broad:
        notes.insert(1, broad)
    if days_now != days_before:
        notes.append("Periods differ in length, so volumes are per-day averages.")
    return DriverReport("contact_volume", "calls", totals[0], totals[1], rank_segments(per_dim), notes, log)


def broad_based(per_dim: dict[str, list[Segment]], overall: float | None, min_share: float = 0.01) -> str | None:
    """If nearly every sizeable segment moved by about the overall percentage, the move is not local."""
    if not overall:
        return None
    changes = []
    for segs in per_dim.values():
        total0 = sum(s.before or 0 for s in segs)
        for s in segs:
            if s.before and total0 and s.before / total0 >= min_share:
                changes.append(s.after / s.before - 1)
    if len(changes) < 4 or max(abs(c - overall) for c in changes) > BROAD_SPREAD:
        return None
    return (f"Broad-based: every sizeable segment moved by {min(changes):+.0%} to {max(changes):+.0%} "
            f"(overall {overall:+.0%}), so the change is not confined to one site, province or hour. "
            "That pattern is consistent with a system-wide cause; the records needed to confirm one are not connected.")
