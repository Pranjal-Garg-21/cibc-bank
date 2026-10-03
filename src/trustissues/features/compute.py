"""Run the feature SQL over a spine and write the feature files.

    python -m trustissues.features.compute           # promise model: spine = labelled promises
    python -m trustissues.features.compute --case    # case model:    spine = (case, month-end) rows

Every file in features/sql/ reads the temp table `spine` plus the src_* views from features/sources.py and
returns `row_id` plus features, using only data dated strictly before `event_ts`. The spine has one row per
thing being scored:

    row_id, ptp_id, case_id, golden_customer_id, crm_customer_id, account_id, event_ts, ptp_amount, ptp_due_date,
    customer_stated_income_date

- promise spine: row_id = ptp_id, event_ts = when the promise was made, promise columns from that promise
- case spine: row_id = case_id@date, event_ts = that date, promise columns from the promise open then (if any)

Writes (promise model, the Feast offline sources):
- warehouse/p3/feature_views/<group>.parquet: ptp_id, golden_customer_id, crm_customer_id, event_ts + that group's features
- warehouse/p3/features.parquet: the spine (label, split) joined with every group
Writes (case model): warehouse/p3/case_features.parquet
"""
from __future__ import annotations

import argparse
from pathlib import Path

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.features import sources
from trustissues.features.build_training_set import SPINE_FILE

SQL_DIR = Path(__file__).parent / "sql"
GROUPS = ["promise_at_creation", "promise_history", "delinquency", "liquidity", "payday", "channel_response"]
CASE_GROUPS = ["case_state", "promise_history", "delinquency", "liquidity", "payday", "channel_response"]
CATEGORICAL = ["ptp_type", "ptp_channel", "payment_method_promised", "product"]
CASE_CATEGORICAL = ["primary_product", "case_open_reason"]
FEATURES_FILE = "features.parquet"
CASE_FEATURES_FILE = "case_features.parquet"
CASE_SPINE_FILE = "case_spine.parquet"
VIEWS_DIR = "feature_views"


def group_sql(group: str) -> str:
    return (SQL_DIR / f"{group}.sql").read_text(encoding="utf-8")


def register_spine(con, spine_sql: str) -> None:
    """Promise spine. spine_sql returns ptp_id and event_ts; promise columns come from the untruncated promises."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE spine AS
        SELECT sp.ptp_id AS row_id, sp.ptp_id, p.case_id, p.golden_customer_id, p.crm_customer_id, p.account_id, sp.event_ts,
               p.ptp_amount, p.ptp_due_date, p.customer_stated_income_date
        FROM ({spine_sql}) sp JOIN ({sources.sql("promises")}) p USING (ptp_id)""")


def register_case_spine(con, spine_sql: str) -> None:
    """Case spine. spine_sql returns row_id, case_id and event_ts. Case columns come from the untruncated case
    record; promise columns from the case's latest promise made before event_ts and not resolved before it."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE spine AS
        WITH sp AS ({spine_sql}),
        open_ptp AS (
            SELECT sp.row_id, arg_max(p.ptp_id, p.ptp_created_ts) AS ptp_id
            FROM sp JOIN ({sources.sql("promises")}) p
              ON p.case_id = sp.case_id AND p.ptp_created_ts < sp.event_ts
             AND NOT (p.ptp_status <> 'open' AND p.status_ts < sp.event_ts)  -- not resolved yet (case_state.sql)
            GROUP BY sp.row_id
        )
        SELECT sp.row_id, o.ptp_id, sp.case_id, c.golden_customer_id, c.crm_customer_id, c.primary_account_id AS account_id,
               sp.event_ts,
               p.ptp_amount, p.ptp_due_date, p.customer_stated_income_date
        FROM sp
        JOIN ({sources.sql("case_history")}) c USING (case_id)
        LEFT JOIN open_ptp o USING (row_id)
        LEFT JOIN ({sources.sql("promises")}) p ON p.ptp_id = o.ptp_id""")


def compute(con, groups=GROUPS) -> dict[str, list[str]]:
    """Materialise feat_<group> temp tables. Sources (src_*) and `spine` must be registered first."""
    cols = {}
    for g in groups:
        con.execute(f"CREATE OR REPLACE TEMP TABLE feat_{g} AS {group_sql(g)}")
        names = [c for c in con.table(f"feat_{g}").columns if c != "row_id"]
        leaked = set(names) & sources.LEAKAGE_COLUMNS
        if leaked:
            raise ValueError(f"{g}.sql returns outcome columns {sorted(leaked)}")
        n, distinct = con.execute(f"SELECT count(*), count(DISTINCT row_id) FROM feat_{g}").fetchone()
        if n != distinct:
            raise ValueError(f"{g}.sql returns {n - distinct} duplicate row_id rows")
        cols[g] = names
    return cols


def joined_sql(cols: dict[str, list[str]], extra: str = "") -> str:
    """One row per spine row: row_id (+ extra spine columns) and every group's features."""
    seen, select = set(), []
    for g, names in cols.items():
        for c in names:
            if c not in seen:  # shared groups never repeat a name, but be safe
                seen.add(c)
                select.append(f'f_{g}."{c}"')
    joins = " ".join(f"LEFT JOIN feat_{g} f_{g} USING (row_id)" for g in cols)
    return f"SELECT s.row_id{extra}, {', '.join(select)} FROM spine s {joins}"


def summarize(con, path: Path, feats: list[str]) -> None:
    summary = con.execute(f"SUMMARIZE SELECT {', '.join(feats)} FROM read_parquet('{path.as_posix()}')").fetchall()
    print(f"\n{'feature':34} {'type':9} {'null %':>7} {'min':>12} {'max':>12} {'mean':>12}")
    for name, type_, mn, mx, _, avg, *_rest, count, null_pct in summary:
        print(f"{name:34} {type_[:9]:9} {float(null_pct):7.1f} {str(mn)[:12]:>12} {str(mx)[:12]:>12} "
              f"{(str(round(float(avg), 3)) if avg is not None else '')[:12]:>12}")


def run_promise(con) -> None:
    spine_path = (config.p3_dir() / SPINE_FILE).as_posix()
    register_spine(con, f"SELECT ptp_id, event_ts FROM read_parquet('{spine_path}')")
    cols = compute(con)

    out_dir = config.p3_dir() / VIEWS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for g, names in cols.items():
        sel = ", ".join(f'f."{c}"' for c in names)
        con.execute(f"""COPY (SELECT s.row_id AS ptp_id, s.golden_customer_id, s.crm_customer_id, s.event_ts, {sel}
                              FROM spine s JOIN feat_{g} f USING (row_id) ORDER BY s.event_ts)
                        TO '{(out_dir / f"{g}.parquet").as_posix()}' (FORMAT PARQUET)""")

    out = config.p3_dir() / FEATURES_FILE
    con.execute(f"""COPY (SELECT l.label, l.split, j.* RENAME (row_id AS ptp_id)
                          FROM ({joined_sql(cols, ", s.golden_customer_id, s.crm_customer_id, s.event_ts")}) j
                          JOIN read_parquet('{spine_path}') l ON l.ptp_id = j.row_id ORDER BY j.event_ts)
                    TO '{out.as_posix()}' (FORMAT PARQUET)""")
    print(f"wrote {out} and {len(cols)} feature views in {out_dir}")
    summarize(con, out, [c for names in cols.values() for c in names])


def run_case(con) -> None:
    spine_path = (config.p3_dir() / CASE_SPINE_FILE).as_posix()
    register_case_spine(con, f"SELECT row_id, case_id, event_ts FROM read_parquet('{spine_path}')")
    cols = compute(con, CASE_GROUPS)
    out = config.p3_dir() / CASE_FEATURES_FILE
    con.execute(f"""COPY (SELECT l.label, l.split, l.as_of_date, j.*
                          FROM ({joined_sql(cols, ", s.case_id, s.golden_customer_id, s.crm_customer_id, s.event_ts")}) j
                          JOIN read_parquet('{spine_path}') l USING (row_id) ORDER BY j.event_ts, j.row_id)
                    TO '{out.as_posix()}' (FORMAT PARQUET)""")
    print(f"wrote {out}")
    summarize(con, out, [c for names in cols.values() for c in names])


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute point-in-time features")
    ap.add_argument("--case", action="store_true", help="case-level spine instead of promises")
    args = ap.parse_args()
    con = connect(read_only=True)
    sources.register(con)
    (run_case if args.case else run_promise)(con)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
