"""Case-level training spine: one row per collections case open at a month-end.

    python -m trustissues.features.build_case_training_set

Label (1 = bad, like the promise model): the case did NOT cure within horizon_days after the month-end.
Cure = the case was paid up and closed (cure_date is set for cured, settled and closed_paid cases).

A case is open at month-end t if it was opened before t and not closed by t. Rows are kept only while the
label window has been fully observed: t + horizon_days <= the last cure date in the data (outcomes are
recorded with a lag, so this is earlier than AS_OF_DATE).

Split by month-end (config/settings.yaml, p3.case_model): train before valid_start, valid until
holdout_start, holdout from then on. Holdout month-ends match the incumbent cure_30d score dates.

Writes warehouse/p3/case_spine.parquet.
"""
from __future__ import annotations

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.features import sources
from trustissues.features.compute import CASE_SPINE_FILE


def spine_sql(cutoff) -> str:
    cm = config.settings()["p3"]["case_model"]
    h = int(cm["horizon_days"])
    return f"""
        WITH dates AS (
            SELECT CAST(last_day(d) AS DATE) AS t
            FROM generate_series(DATE '{cm["first_as_of"]}', DATE '{cutoff}' - INTERVAL {h} DAY, INTERVAL 1 MONTH) g(d)
        ),
        c AS (
            SELECT case_id, CAST(case_open_ts AS DATE) AS opened, cure_date,
                   CASE WHEN case_status = 'open' THEN NULL ELSE case_status_date END AS closed
            FROM src_case_history
        )
        SELECT c.case_id || '@' || strftime(d.t, '%Y-%m-%d') AS row_id, c.case_id, d.t AS as_of_date,
               CAST(d.t AS TIMESTAMP) AS event_ts,
               CAST(NOT coalesce(c.cure_date > d.t AND c.cure_date <= d.t + {h}, false) AS INTEGER) AS label,
               CASE WHEN d.t >= DATE '{cm["holdout_start"]}' THEN 'holdout'
                    WHEN d.t >= DATE '{cm["valid_start"]}' THEN 'valid' ELSE 'train' END AS split
        FROM dates d JOIN c ON c.opened < d.t AND (c.closed IS NULL OR c.closed > d.t)
        WHERE d.t + {h} <= DATE '{cutoff}'
        ORDER BY d.t, c.case_id
    """


def main() -> int:
    con = connect(read_only=True)
    sources.register(con)
    cutoff = con.execute("SELECT max(cure_date) FROM src_case_history").fetchone()[0]
    con.execute(f"CREATE OR REPLACE TEMP TABLE case_spine AS {spine_sql(cutoff)}")
    out = config.p3_dir() / CASE_SPINE_FILE
    con.execute(f"COPY case_spine TO '{out.as_posix()}' (FORMAT PARQUET)")
    print(f"wrote {out} (outcomes recorded up to {cutoff})")
    print(con.sql("""SELECT split, count(*) AS rows, count(DISTINCT case_id) AS cases, round(avg(label), 4) AS not_cured_rate,
                            min(as_of_date) AS first, max(as_of_date) AS last
                     FROM case_spine GROUP BY 1 ORDER BY first"""))
    dupes = con.execute("SELECT count(*) - count(DISTINCT row_id) FROM case_spine").fetchone()[0]
    print(("PASS" if dupes == 0 else "FAIL") + f"  one row per case and month-end ({dupes} duplicates)")
    return 1 if dupes else 0


if __name__ == "__main__":
    raise SystemExit(main())
