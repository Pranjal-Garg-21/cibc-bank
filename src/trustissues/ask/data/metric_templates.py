"""Load bounded metric templates; never interpolate unapproved identifiers or scalar values."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .semantic import SQL_DIR


METRICS = frozenset({"roll_rate", "cure_rate", "promise_kept_rate", "broken_promise_rate", "rpc_rate",
                     "ptp_rate", "cost_per_contact", "cost_per_rpc", "collected_amount", "hardship_rate",
                     "delinquency_rate", "write_off_rate", "average_dpd", "contact_attempts_per_case",
                     "payroll_delay_share", "out_of_hours_share", "dispute_count"})
DIMENSIONS = {
    "promises": {x: x for x in ("product", "agent_site", "queue", "test_cell", "prior_broken_ptp_count", "ptp_channel")},
    "contacts": {x: x for x in ("channel", "contact_hour_local", "province", "agent_site")},
    "cases": {x: x for x in ("primary_product", "queue", "province", "reported_bucket", "canonical_bucket")},
    "accounts": {x: x for x in ("product", "product_code")},
    "snapshots": {"product": "a.product"}, "payroll": {},
    "out_of_hours": {"province": "c.province"}, "disputes": {},
}
FAMILIES = {
    **dict.fromkeys(("promise_kept_rate", "broken_promise_rate", "collected_amount"), "promises"),
    **dict.fromkeys(("rpc_rate", "ptp_rate", "cost_per_contact", "cost_per_rpc"), "contacts"),
    **dict.fromkeys(("cure_rate", "hardship_rate", "average_dpd", "contact_attempts_per_case"), "cases"),
    **dict.fromkeys(("delinquency_rate", "write_off_rate"), "accounts"),
    "roll_rate": "snapshots", "payroll_delay_share": "payroll",
    "out_of_hours_share": "out_of_hours", "dispute_count": "disputes",
}


@dataclass(frozen=True)
class MetricQuery:
    metric_id: str
    variant: str
    sql: str
    parameters: dict[str, object]


def render_metric(metric_id: str, *, parameters: dict[str, object], dimensions: tuple[str, ...] = (),
                  cap_at: int | None = None, variant: str = "default") -> MetricQuery:
    if metric_id not in METRICS:
        raise ValueError("Unknown metric")
    if metric_id == "out_of_hours_share" and dimensions != ("province",):
        raise ValueError("out_of_hours_share requires grouping by province")
    if variant not in ({"default", "cohort_30d", "certification_pilot"} if metric_id == "cure_rate" else {"default"}):
        raise ValueError("Unsupported metric variant")
    if variant == "certification_pilot" and dimensions:
        raise ValueError("The certification pilot is grouped by agent certification only")
    if len(set(dimensions)) != len(dimensions) or len(dimensions) > 3:
        raise ValueError("Invalid grouping dimensions")
    allowed = DIMENSIONS[FAMILIES[metric_id]]
    if any(d not in allowed for d in dimensions):
        raise ValueError("Grouping dimension not approved")
    if cap_at is not None and (not isinstance(cap_at, int) or isinstance(cap_at, bool) or not 1 <= cap_at <= 20 or
                              "prior_broken_ptp_count" not in dimensions):
        raise ValueError("cap_at requires prior_broken_ptp_count and a cap in 1..20")
    grouped = []
    for d in dimensions:
        expr = allowed[d]
        if metric_id == "contact_attempts_per_case":
            expr = "c." + expr
        if d == "prior_broken_ptp_count" and cap_at is not None:
            expr = f"least({expr}, {cap_at})"
        grouped.append(f'{expr} AS "{d}"')
    name = {"cohort_30d": "cure_rate_cohort", "certification_pilot": "cure_rate_pilot"}.get(variant, metric_id)
    sql = (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")
    sql = sql.replace("{group_select}", ", ".join(grouped) + (", " if grouped else ""))
    sql = sql.replace("{group_by}", "GROUP BY " + ", ".join(str(i+1) for i in range(len(grouped))) if grouped else "")
    # Parse parameter names structurally; callers must provide exactly the required scalar parameters.
    from sqlglot import parse_one, exp
    names = {p.name for p in parse_one(sql, read="duckdb").find_all(exp.Placeholder)}
    inputs = names - {"period_label", "comparison_label"} if metric_id == "dispute_count" else names
    if set(parameters) != inputs:
        raise ValueError(f"Expected parameters {sorted(inputs)}; received {sorted(parameters)}")
    for key, value in parameters.items():
        if value is not None and not isinstance(value, (str, int, float, bool, date, datetime)):
            raise ValueError(f"Non-scalar parameter: {key}")
        if key in {"random_only", "exclude_holds", "hardship_only"} and not isinstance(value, bool):
            raise ValueError(f"Boolean parameter required: {key}")
        if key in {"start_dpd_min", "start_dpd_max"} and (type(value) is not int or value < 0):
            raise ValueError(f"Non-negative integer parameter required: {key}")
        if key in {"start", "end", "comparison_start", "comparison_end", "snapshot_date", "start_snapshot", "end_snapshot", "observation_end"}:
            if not isinstance(value, (date, datetime, str)):
                raise ValueError(f"Date parameter required: {key}")
            if isinstance(value, str):
                try:
                    datetime.fromisoformat(value)
                except ValueError as e:
                    raise ValueError(f"ISO date/timestamp required: {key}") from e
    for first, last in (("start", "end"), ("comparison_start", "comparison_end"), ("start_snapshot", "end_snapshot")):
        if first in parameters and last in parameters:
            def timestamp(value):
                if isinstance(value, datetime):
                    return value
                if isinstance(value, date):
                    return datetime.combine(value, datetime.min.time())
                return datetime.fromisoformat(value)
            try:
                increasing = timestamp(parameters[first]) < timestamp(parameters[last])
            except (TypeError, ValueError) as e:
                raise ValueError("Date boundaries must have compatible time zones") from e
            if not increasing:
                raise ValueError("Date window must have increasing boundaries")
    parameters = dict(parameters)
    if metric_id == "dispute_count":
        def boundary(key):
            value = parameters[key]
            if isinstance(value, str):
                value = datetime.fromisoformat(value)
            if isinstance(value, datetime):
                if value.time() != datetime.min.time() or value.tzinfo is not None:
                    raise ValueError("Dispute periods require local midnight date boundaries")
                value = value.date()
            return value
        start, end, previous, previous_end = (boundary(k) for k in
                                             ("start", "end", "comparison_start", "comparison_end"))
        if start < previous_end and previous < end:
            raise ValueError("Dispute periods must not overlap")
        months = ('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')
        def label(first, exclusive_end):
            last = exclusive_end - timedelta(days=1)
            if first.year == last.year and first.month == last.month:
                return f"{first.day}-{last.day} {months[first.month-1]}"
            return f"{first.day} {months[first.month-1]} {first.year}-{last.day} {months[last.month-1]} {last.year}"
        parameters.update(period_label=label(start,end), comparison_label=label(previous,previous_end))
    if metric_id == "roll_rate" and (parameters['start_dpd_min'],parameters['start_dpd_max']) not in {
        (0,0),(1,30),(31,60),(61,90),(91,120),(121,150),(151,180),
    }:
        raise ValueError("Roll-rate starting range must be a canonical bucket with a possible worse bucket")
    return MetricQuery(metric_id, variant, sql, dict(parameters))
