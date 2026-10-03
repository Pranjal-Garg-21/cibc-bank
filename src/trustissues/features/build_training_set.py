"""Training set spine: one row per labelled promise, with its event time and split.

    python -m trustissues.features.build_training_set

Label (metric_definitions.promise_kept_rate): 1 = broken or partially_kept, 0 = kept. Open and
cancelled promises are dropped.

Split (dates in config/settings.yaml, p3):
- holdout: resolved promises the incumbent ptp_break model scored (scores start at split_date)
- valid:   created in [valid_start, split_date), not in holdout
- train:   created before valid_start, not in holdout
Promises created on or after split_date that the incumbent did not score are not used.

Writes warehouse/p3/training_spine.parquet.
"""
from __future__ import annotations

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.features import sources

SPINE_FILE = "training_spine.parquet"
BROKEN = ("broken", "partially_kept")
RESOLVED = ("kept", "broken", "partially_kept")


def spine_sql() -> str:
    p3 = config.settings()["p3"]
    resolved = ", ".join(f"'{s}'" for s in RESOLVED)
    broken = ", ".join(f"'{s}'" for s in BROKEN)
    return f"""
        WITH scored AS (SELECT DISTINCT ptp_id FROM src_incumbent_ptp_scores),
        labelled AS (
            SELECT p.ptp_id, p.golden_customer_id, p.crm_customer_id, p.account_id, p.case_id,
                   p.ptp_created_ts AS event_ts,
                   CAST(p.ptp_status IN ({broken}) AS INTEGER) AS label,
                   CASE WHEN s.ptp_id IS NOT NULL THEN 'holdout'
                        WHEN p.ptp_created_ts >= DATE '{p3["split_date"]}' THEN NULL
                        WHEN p.ptp_created_ts >= DATE '{p3["valid_start"]}' THEN 'valid'
                        ELSE 'train' END AS split
            FROM src_promises p LEFT JOIN scored s USING (ptp_id)
            WHERE p.ptp_status IN ({resolved})
        )
        SELECT * FROM labelled WHERE split IS NOT NULL ORDER BY event_ts, ptp_id
    """


def build(con) -> dict:
    sources.register(con)
    con.execute(f"CREATE OR REPLACE TEMP TABLE spine AS {spine_sql()}")
    unused = con.execute(f"""
        SELECT count(*) FROM src_promises p
        WHERE p.ptp_status IN ({", ".join(f"'{s}'" for s in RESOLVED)})
          AND p.ptp_id NOT IN (SELECT ptp_id FROM spine)""").fetchone()[0]
    out = config.p3_dir() / SPINE_FILE
    con.execute(f"COPY spine TO '{out.as_posix()}' (FORMAT PARQUET)")
    return {"path": out, "unused_resolved_after_split": unused}


def checks(con) -> list[tuple[bool, str]]:
    """Done-when checks for step 1, run against the temp table `spine`."""
    rows = dict(con.execute("SELECT split, count(*) FROM spine GROUP BY 1").fetchall())
    scored_resolved = con.execute(f"""
        SELECT count(DISTINCT s.ptp_id) FROM src_incumbent_ptp_scores s JOIN src_promises p USING (ptp_id)
        WHERE p.ptp_status IN ({", ".join(f"'{s}'" for s in RESOLVED)})""").fetchone()[0]
    max_train, min_holdout = con.execute("""
        SELECT max(event_ts) FILTER (WHERE split = 'train'), min(event_ts) FILTER (WHERE split = 'holdout')
        FROM spine""").fetchone()
    max_valid = con.execute("SELECT max(event_ts) FROM spine WHERE split = 'valid'").fetchone()[0]
    dupes = con.execute("SELECT count(*) - count(DISTINCT ptp_id) FROM spine").fetchone()[0]
    split_date = config.settings()["p3"]["split_date"]
    return [
        (rows.get("holdout") == scored_resolved,
         f"holdout has {rows.get('holdout', 0):,} rows = resolved promises with an incumbent score ({scored_resolved:,})"),
        (max_train < min_holdout, f"no holdout promise created before a training promise "
                                  f"(last train {max_train}, first holdout {min_holdout})"),
        (str(max_valid) < split_date, f"valid ends before the split date (last valid {max_valid})"),
        (dupes == 0, f"one row per promise ({dupes} duplicates)"),
    ]


def main() -> int:
    con = connect(read_only=True)
    res = build(con)
    print(f"wrote {res['path']}")
    print(con.sql("""
        SELECT split, count(*) AS rows, sum(label) AS broken, count(*) - sum(label) AS kept,
               round(avg(label), 4) AS broken_rate, min(event_ts) AS first, max(event_ts) AS last
        FROM spine GROUP BY split ORDER BY first"""))
    print(f"resolved promises not used (created on/after split date, no incumbent score): "
          f"{res['unused_resolved_after_split']:,}")
    failed = 0
    for ok, msg in checks(con):
        failed += not ok
        print(("PASS  " if ok else "FAIL  ") + msg)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
