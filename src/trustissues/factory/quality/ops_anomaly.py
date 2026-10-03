"""Three-sigma detection over historical observations, not a mutable latest manifest."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from statistics import mean, stdev
from math import isfinite
from typing import Iterable


@dataclass(frozen=True)
class Observation:
    source: str
    metric: str  # row_count | null_rate:<column>
    value: float
    observed_at: datetime


@dataclass(frozen=True)
class OpsEvent:
    event_id: str
    affected_tables: tuple[str, ...]
    start_ts: datetime
    end_ts: datetime | None
    description: str = ""


@dataclass(frozen=True)
class Anomaly:
    observation: Observation
    status: str  # normal | insufficient_history | explained | alert
    baseline_mean: float | None
    baseline_std: float | None
    event_ids: tuple[str, ...] = ()


def detect(current: Observation, history: Iterable[Observation], events: Iterable[OpsEvent] = (), *,
           sigma: float = 3., minimum_history: int = 3, window: int = 20) -> Anomaly:
    if sigma <= 0 or minimum_history < 2 or window < minimum_history:
        raise ValueError("Invalid baseline configuration")
    if not isfinite(current.value) or current.value < 0 or (current.metric.startswith("null_rate:") and current.value > 1):
        raise ValueError("Observation outside its valid range")
    earlier = sorted((o for o in history if o.source == current.source and o.metric == current.metric and
                      o.observed_at < current.observed_at), key=lambda o: o.observed_at)[-window:]
    if len(earlier) < minimum_history:
        return Anomaly(current, "insufficient_history", None, None)
    values = [o.value for o in earlier]
    if not all(isfinite(value) and value >= 0 for value in values):
        raise ValueError("Invalid historical baseline values")
    center, spread = mean(values), stdev(values)
    if abs(current.value-center) <= sigma*spread:
        return Anomaly(current, "normal", center, spread)
    matching = tuple(sorted(e.event_id for e in events if current.source in e.affected_tables and
                           e.start_ts <= current.observed_at and (e.end_ts is None or current.observed_at <= e.end_ts)))
    # 'explained' means correlated with an incident; report must not claim causal proof.
    return Anomaly(current, "explained" if matching else "alert", center, spread, matching)


def events_from_rows(rows) -> list[OpsEvent]:
    """Rows: event_id, affected_tables (JSON array), start_ts, end_ts, description."""
    return [OpsEvent(eid, tuple(json.loads(tables)), start, end, description or "")
            for eid, tables, start, end, description in rows]


def manifest_counts(con) -> dict[str, int]:
    """Read latest per-source manifest totals; caller must store each observation historically."""
    return dict(con.execute("SELECT source,sum(row_count) FROM raw._load_manifest GROUP BY source").fetchall())


def record_observation(con, observation: Observation) -> None:
    """Explicit batch hook; one observation per source/metric/timestamp, separate from manifest."""
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute("CREATE TABLE IF NOT EXISTS raw._quality_observations (source VARCHAR,metric VARCHAR,"
                "value DOUBLE,observed_at TIMESTAMP,PRIMARY KEY(source,metric,observed_at))")
    con.execute("INSERT OR REPLACE INTO raw._quality_observations VALUES (?,?,?,?)",
                [observation.source,observation.metric,observation.value,observation.observed_at])


def load_history(con, current: Observation, *, window: int = 20) -> list[Observation]:
    if window < 1:
        raise ValueError("window must be positive")
    rows=con.execute("SELECT source,metric,value,observed_at FROM raw._quality_observations "
                     "WHERE source=? AND metric=? AND observed_at<? ORDER BY observed_at DESC LIMIT ?",
                     [current.source,current.metric,current.observed_at,window]).fetchall()
    return [Observation(*row) for row in rows]
