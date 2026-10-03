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
        c = duckdb.connect(str(path), read_only=True, config=config.duckdb_limits())
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
    rows = con.execute(f"""
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
        -- filter first, then sample (DuckDB samples a FROM clause before applying its WHERE)
        (SELECT * FROM (SELECT ptp_id, event_ts FROM cand WHERE pending_outcome)
         USING SAMPLE {SAMPLE // 2} ROWS (reservoir, 7))
        UNION ALL
        (SELECT * FROM (SELECT ptp_id, event_ts FROM cand WHERE NOT pending_outcome AND history >= 2)
         USING SAMPLE {SAMPLE - SAMPLE // 2} ROWS (reservoir, 7))
        ORDER BY ptp_id""").fetchall()
    assert len(rows) == SAMPLE
    return rows


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
    assert any(f["worst_dpd_12m"] is not None for f in full.values())


@pytest.fixture(scope="module")
def case_sample(con):
    """Case-months right on the day-k boundaries (event time = k days after opening, so day-k arrears become
    known exactly then), in cases with promises and contacts around the event time."""
    sources.register(con)
    rows = []
    for k in (7, 14, 30, 45):
        rows += con.execute(f"""
            SELECT * FROM (
                SELECT c.case_id || '@{k}', c.case_id, c.case_open_ts + INTERVAL {k} DAY AS event_ts
                FROM src_case_history c
                WHERE c.case_open_ts >= TIMESTAMP '2025-06-01' AND c.dpd_at_day_45 IS NOT NULL
                  AND c.case_id IN (SELECT case_id FROM src_promises))
            USING SAMPLE {SAMPLE // 4} ROWS (reservoir, 11)""").fetchall()
    return rows


def _case_features(con, rows, as_of=None) -> dict:
    sources.register(con, as_of=as_of)
    values = ", ".join(f"('{r}-{i}', '{c}', TIMESTAMP '{e}')" for i, (r, c, e) in enumerate(rows))
    compute.register_case_spine(con, f"SELECT * FROM (VALUES {values}) v(row_id, case_id, event_ts)")
    cols = compute.compute(con, compute.CASE_GROUPS)
    rel = con.sql(compute.joined_sql(cols))
    return {r[0]: dict(zip(rel.columns[1:], r[1:])) for r in rel.fetchall()}


def test_case_features_ignore_events_at_or_after_the_event_time(con, case_sample):
    full = _case_features(con, case_sample)
    diffs = []
    for i, (r, c, e) in enumerate(case_sample):
        truncated = _case_features(con, [(r, c, e)], as_of=e)[f"{r}-0"]
        diffs += [f"{c} @ {e} {k}: full={full[f'{r}-{i}'][k]} truncated={v}"
                  for k, v in truncated.items() if not _same(full[f"{r}-{i}"][k], v)]
    sources.register(con)
    assert not diffs, "\n".join(diffs[:30])


def test_case_sample_exercises_the_features(con, case_sample):
    full = _case_features(con, case_sample)
    assert any(f["case_age_days"] > 30 and f["dpd_now"] is not None for f in full.values())
    assert any(f["case_contacts"] for f in full.values())
    assert any(f["case_promises"] for f in full.values())


def test_a_promise_still_open_counts_as_open_when_scoring_today(con):
    """Open promises carry status_ts = their creation time. Scoring at the as-of date must still see them as open
    (until 3 Oct the case features called every currently open promise resolved)."""
    sources.register(con)
    rows = con.execute(f"""
        SELECT c.case_id || '@live', c.case_id, TIMESTAMP '{config.AS_OF_DATE}' AS event_ts
        FROM src_cases c JOIN src_promises p ON p.ptp_id = c.current_ptp_id
        WHERE c.case_status = 'open' AND p.ptp_status = 'open' AND p.ptp_created_ts < TIMESTAMP '{config.AS_OF_DATE}'
        ORDER BY c.case_id LIMIT 30""").fetchall()
    assert rows, "no open case with an open promise"
    feats = _case_features(con, rows)
    assert all(f["has_open_ptp"] == 1 and f["open_ptp_to_overdue_ratio"] is not None for f in feats.values())
