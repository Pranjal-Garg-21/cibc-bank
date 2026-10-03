"""Point-in-time leakage test for features/sql (P3 plan step 3).

For a sample of real promises, move the event time one day earlier (E = created - 1 day) and build
the features twice: once on the full warehouse, once on sources truncated to what was known strictly
before E (sources.register(as_of=E)). Any feature that reads an event at or after E differs.

promise_at_creation is not tested here: its columns are attributes of the promise itself, which is
recorded at its own creation time.

Needs a warehouse with raw (or curated) loaded; skipped otherwise.
"""
import math

import duckdb
import pytest

from trustissues.common import config
from trustissues.features import compute, sources

GROUPS = [g for g in compute.GROUPS if g != "promise_at_creation"]
SAMPLE = 30


@pytest.fixture(scope="module")
def con():
    path = config.warehouse_path()
    if not path.exists():
        pytest.skip("no warehouse built")
    try:
        c = duckdb.connect(str(path), read_only=True)
        c.execute(f"SELECT 1 FROM {sources.SCHEMA}.promises_to_pay LIMIT 1")
    except duckdb.Error as e:
        pytest.skip(f"warehouse not readable: {e}")
    return c


@pytest.fixture(scope="module")
def sample(con):
    """Hard cases: half where another promise of the same customer was still unresolved at the event time
    (its outcome lands after E, which is what a look-ahead would read), half from customers with plenty
    of history, so every feature group has something to read."""
    sources.register(con)
    return con.execute(f"""
        WITH cand AS (
            SELECT p.ptp_id, p.ptp_created_ts - INTERVAL 1 DAY AS event_ts,
                   bool_or(q.status_ts >= p.ptp_created_ts - INTERVAL 1 DAY
                           AND q.status_ts < p.ptp_created_ts + INTERVAL 30 DAY) AS pending_outcome,
                   count(q.ptp_id) AS history
            FROM src_promises p
            JOIN src_promises q ON q.crm_customer_id = p.crm_customer_id AND q.ptp_id <> p.ptp_id
                               AND q.ptp_created_ts < p.ptp_created_ts - INTERVAL 1 DAY
            WHERE p.ptp_created_ts >= TIMESTAMP '2025-01-01'
            GROUP BY p.ptp_id, p.ptp_created_ts
        )
        (SELECT ptp_id, event_ts FROM cand WHERE pending_outcome USING SAMPLE {SAMPLE // 2} ROWS (reservoir, 7))
        UNION ALL
        (SELECT ptp_id, event_ts FROM cand WHERE NOT pending_outcome AND history >= 2
         USING SAMPLE {SAMPLE - SAMPLE // 2} ROWS (reservoir, 7))
        ORDER BY ptp_id""").fetchall()


def _features(con, rows, as_of=None) -> dict:
    sources.register(con, as_of=as_of)
    values = ", ".join(f"('{p}', TIMESTAMP '{e}')" for p, e in rows)
    compute.register_spine(con, f"SELECT * FROM (VALUES {values}) v(ptp_id, event_ts)")
    cols = compute.compute(con, GROUPS)
    rel = con.sql(compute.joined_sql(cols))
    return {r[0]: dict(zip(rel.columns[1:], r[1:])) for r in rel.fetchall()}


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


def test_features_ignore_events_at_or_after_the_event_time(con, sample):
    full = _features(con, sample)
    diffs = []
    for ptp_id, event_ts in sample:
        truncated = _features(con, [(ptp_id, event_ts)], as_of=event_ts)[ptp_id]
        diffs += [f"{ptp_id} {k}: full={full[ptp_id][k]} truncated={v}"
                  for k, v in truncated.items() if not _same(full[ptp_id][k], v)]
    sources.register(con)
    assert not diffs, "\n".join(diffs)


def test_sample_exercises_the_features(con, sample):
    """Guard against a vacuous pass: the sample must actually have history to read."""
    full = _features(con, sample)
    assert any(f["contacts_30d"] for f in full.values())
    assert any(f["resolved_ptp_all"] for f in full.values())
    assert any(f["snapshots_observed_12m"] for f in full.values())
