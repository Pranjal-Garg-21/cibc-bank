"""Run the feature SQL over the training spine and write the feature files.

    python -m trustissues.features.compute

Each file in features/sql/ reads the temp table `spine` (one row per promise: ptp_id, crm_customer_id,
account_id, case_id, event_ts, and the promise's own ptp_amount / ptp_due_date /
customer_stated_income_date) plus the src_* views from features/sources.py, and returns ptp_id plus
features, using only data dated strictly before event_ts.

Writes:
- warehouse/p3/feature_views/<group>.parquet: ptp_id, crm_customer_id, event_ts + that group's features
  (the Feast offline sources, one per SQL file)
- warehouse/p3/features.parquet: the spine (label, split) joined with every group
"""
from __future__ import annotations

from pathlib import Path

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.features import sources
from trustissues.features.build_training_set import SPINE_FILE

SQL_DIR = Path(__file__).parent / "sql"
GROUPS = ["promise_at_creation", "promise_history", "delinquency", "liquidity", "payday", "channel_response"]
CATEGORICAL = ["ptp_type", "ptp_channel", "payment_method_promised", "product"]
FEATURES_FILE = "features.parquet"
VIEWS_DIR = "feature_views"
SPINE_KEYS = ["ptp_id", "crm_customer_id", "account_id", "case_id", "event_ts"]


def group_sql(group: str) -> str:
    return (SQL_DIR / f"{group}.sql").read_text(encoding="utf-8")


def register_spine(con, spine_sql: str) -> None:
    """spine_sql returns at least ptp_id and event_ts. Promise attributes come from the untruncated promises."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE spine AS
        SELECT sp.ptp_id, p.crm_customer_id, p.account_id, p.case_id, sp.event_ts,
               p.ptp_amount, p.ptp_due_date, p.customer_stated_income_date
        FROM ({spine_sql}) sp JOIN ({sources.sql("promises")}) p USING (ptp_id)""")


def compute(con, groups=GROUPS) -> dict[str, list[str]]:
    """Materialise feat_<group> temp tables. Sources (src_*) and `spine` must be registered first."""
    cols = {}
    for g in groups:
        con.execute(f"CREATE OR REPLACE TEMP TABLE feat_{g} AS {group_sql(g)}")
        names = [c for c in con.table(f"feat_{g}").columns if c != "ptp_id"]
        leaked = set(names) & sources.LEAKAGE_COLUMNS
        if leaked:
            raise ValueError(f"{g}.sql returns outcome columns {sorted(leaked)}")
        n, distinct = con.execute(f"SELECT count(*), count(DISTINCT ptp_id) FROM feat_{g}").fetchone()
        if n != distinct:
            raise ValueError(f"{g}.sql returns {n - distinct} duplicate ptp_id rows")
        cols[g] = names
    return cols


def joined_sql(cols: dict[str, list[str]], extra: str = "") -> str:
    select = ", ".join(f'f_{g}."{c}"' for g, names in cols.items() for c in names)
    joins = " ".join(f"LEFT JOIN feat_{g} f_{g} USING (ptp_id)" for g in cols)
    return f"SELECT s.ptp_id{extra}, {select} FROM spine s {joins}"


def main() -> int:
    con = connect(read_only=True)
    sources.register(con)
    spine_path = (config.p3_dir() / SPINE_FILE).as_posix()
    register_spine(con, f"SELECT ptp_id, event_ts FROM read_parquet('{spine_path}')")
    cols = compute(con)

    out_dir = config.p3_dir() / VIEWS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for g, names in cols.items():
        sel = ", ".join(f'f."{c}"' for c in names)
        con.execute(f"""COPY (SELECT s.ptp_id, s.crm_customer_id, s.event_ts, {sel}
                              FROM spine s JOIN feat_{g} f USING (ptp_id) ORDER BY s.event_ts)
                        TO '{(out_dir / f"{g}.parquet").as_posix()}' (FORMAT PARQUET)""")

    out = config.p3_dir() / FEATURES_FILE
    con.execute(f"""COPY (SELECT l.label, l.split, j.* FROM ({joined_sql(cols, ", s.crm_customer_id, s.event_ts")}) j
                          JOIN read_parquet('{spine_path}') l USING (ptp_id) ORDER BY j.event_ts)
                    TO '{out.as_posix()}' (FORMAT PARQUET)""")
    print(f"wrote {out} and {len(cols)} feature views in {out_dir}")

    feats = [c for names in cols.values() for c in names]
    summary = con.execute(f"SUMMARIZE SELECT {', '.join(feats)} FROM read_parquet('{out.as_posix()}')").fetchall()
    print(f"\n{'feature':34} {'type':9} {'null %':>7} {'min':>12} {'max':>12} {'mean':>12}")
    for name, type_, mn, mx, _, avg, *_rest, count, null_pct in summary:
        print(f"{name:34} {type_[:9]:9} {float(null_pct):7.1f} {str(mn)[:12]:>12} {str(mx)[:12]:>12} "
              f"{(str(round(float(avg), 3)) if avg is not None else '')[:12]:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
