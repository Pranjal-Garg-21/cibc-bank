"""Build curated.<table> from raw.<table>: typed, deduplicated, identity fields standardised.

Generic by design: the column types come from schema.json, so every table in raw is curated
without a hand-written SQL file. A table with no schema entry is passed through as text
(and its columns stay `unclassified`). Per-table behaviour lives in config/curate.yaml.

Rules (plan §3.2):
  - a failed cast becomes NULL, is counted in curated._cast_failures, and the original text is
    kept in <col>__raw on that row
  - exact duplicates are removed on _row_hash (the count removed is recorded)
  - identity fields get std_* columns (see standardise.py)
  - history is kept; <table>_current gives one row per key
"""
from __future__ import annotations

import argparse
import json
import re
import time

import yaml

from trustissues.common import config
from trustissues.common.db import connect

from . import standardise as S

META = ["_ingested_at", "_source_file", "_batch_id", "_row_hash"]
TRUE = "('true','t','1','y','yes')"
FALSE = "('false','f','0','n','no')"


def q(i: str) -> str:
    return '"' + i.replace('"', '""') + '"'


# ---------- type mapping ----------

def kind(schema_type: str | None) -> str:
    t = (schema_type or "string").lower()
    if t == "int":
        return "int"
    if t.startswith("decimal"):
        return "double"
    if t in ("date", "timestamp", "boolean"):
        return t
    return "text"  # string, enum, json, array<...>


def typed_expr(col: str, k: str) -> str:
    c = q(col)
    blank = f"NULLIF(trim({c}), '')"
    if k == "text":
        return blank
    if k == "int":
        return f"TRY_CAST({blank} AS BIGINT)"
    if k == "double":
        return f"TRY_CAST({blank} AS DOUBLE)"
    if k == "date":
        return f"TRY_CAST({blank} AS DATE)"
    if k == "timestamp":
        return f"TRY_CAST({blank} AS TIMESTAMP)"
    if k == "boolean":
        return f"CASE WHEN lower({blank}) IN {TRUE} THEN true WHEN lower({blank}) IN {FALSE} THEN false END"
    raise ValueError(k)


# ---------- UDFs for standardise.py ----------

def register_udfs(con) -> None:
    def t(sql_type: str):
        return con.sql(f"SELECT NULL::{sql_type}").types[0]

    reg = [
        ("std_dob", S.parse_dob, ["VARCHAR"], "STRUCT(dob_iso VARCHAR, dob_alt_iso VARCHAR, dob_ambiguous BOOLEAN)"),
        ("std_phone", S.parse_phone, ["VARCHAR"],
         "STRUCT(phone_e164 VARCHAR, phone_masked BOOLEAN, phone_prefix VARCHAR, phone_suffix VARCHAR)"),
        ("std_postal", S.parse_postal, ["VARCHAR"], "STRUCT(postal VARCHAR, fsa VARCHAR)"),
        ("std_employer", S.normalise_employer, ["VARCHAR"], "VARCHAR"),
    ]
    for name, fn, params, ret in reg:
        con.create_function(name, fn, [t(p) for p in params], t(ret))

    name_t = t("STRUCT(first VARCHAR, middle_initials VARCHAR, last VARCHAR, first_is_initial BOOLEAN, "
               "name_key VARCHAR, name_tokens VARCHAR)")

    def make(order, caps):
        def f(raw):
            r = S.parse_name(raw, order, caps)
            return {k: r[k] for k in ("first", "middle_initials", "last", "first_is_initial", "name_key", "name_tokens")}
        return f

    # one function per (order, caps_default) combination actually used
    for order in ("auto", "first_last", "last_first"):
        for caps in ("first_last", "last_first", "none"):
            con.create_function(f"std_name_{order}_{caps}", make(order, None if caps == "none" else caps),
                                [t("VARCHAR")], name_t)

    def postal_from_address(addr):
        if addr is None:
            return None
        m = re.search(r"([A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d)\s*$", addr.strip())
        return S.parse_postal(m[1])["postal"] if m else None

    con.create_function("std_postal_from_address", postal_from_address, [t("VARCHAR")], t("VARCHAR"))


def identity_select(table: str, cfg: dict) -> list[str]:
    """SQL select items that add std_* columns for one source."""
    items: list[str] = []
    n = cfg.get("name")
    if n:
        fn = f"std_name_{n.get('order', 'auto')}_{n.get('caps_default') or 'none'}"
        c = f"{fn}(raw_{n['col']})"
        for f in ("first", "middle_initials", "last", "first_is_initial", "name_key"):
            items.append(f"{c}.{f} AS std_name_{f}")
    if cfg.get("dob"):
        c = f"std_dob(raw_{cfg['dob']})"
        items += [f"{c}.dob_iso AS std_dob", f"{c}.dob_alt_iso AS std_dob_alt", f"{c}.dob_ambiguous AS std_dob_ambiguous"]
    for label, col in (cfg.get("phones") or {}).items():
        c = f"std_phone(raw_{col})"
        items += [f"{c}.phone_e164 AS std_phone_{label}_e164", f"{c}.phone_masked AS std_phone_{label}_masked",
                  f"{c}.phone_prefix AS std_phone_{label}_prefix", f"{c}.phone_suffix AS std_phone_{label}_suffix"]
    if cfg.get("postal"):
        c = f"std_postal(raw_{cfg['postal']})"
        items += [f"{c}.postal AS std_postal", f"{c}.fsa AS std_fsa"]
    if cfg.get("postal_from_address"):
        items += [f"std_postal_from_address(raw_{cfg['postal_from_address']}) AS std_postal",
                  f"left(std_postal_from_address(raw_{cfg['postal_from_address']}), 3) AS std_fsa"]
    if cfg.get("employer"):
        items.append(f"std_employer(raw_{cfg['employer']}) AS std_employer")
    return items


def identity_source_cols(cfg: dict) -> list[str]:
    cols = []
    if cfg.get("name"):
        cols.append(cfg["name"]["col"])
    for k in ("dob", "postal", "employer", "postal_from_address"):
        if cfg.get(k):
            cols.append(cfg[k])
    cols += list((cfg.get("phones") or {}).values())
    return cols


# ---------- build ----------

def curate_table(con, name: str, schema: dict, curate_cfg: dict) -> dict:
    t0 = time.time()
    raw_cols = [r[0] for r in con.execute(f'DESCRIBE raw.{q(name)}').fetchall()]
    data_cols = [c for c in raw_cols if c not in META]
    types = {c["name"]: c.get("type") for c in (schema.get(name) or {}).get("columns", [])}
    kinds = {c: kind(types.get(c)) for c in data_cols}

    # 1. find columns whose cast loses values (one scan)
    typed_cols = [c for c in data_cols if kinds[c] != "text"]
    failing: dict[str, tuple[int, str | None]] = {}
    if typed_cols:
        aggs = []
        for c in typed_cols:
            aggs.append(f"count(*) FILTER (WHERE NULLIF(trim({q(c)}),'') IS NOT NULL AND {typed_expr(c, kinds[c])} IS NULL)")
            aggs.append(f"any_value({q(c)}) FILTER (WHERE NULLIF(trim({q(c)}),'') IS NOT NULL AND {typed_expr(c, kinds[c])} IS NULL)")
        row = con.execute(f"SELECT {', '.join(aggs)} FROM raw.{q(name)}").fetchone()
        for i, c in enumerate(typed_cols):
            if row[2 * i]:
                failing[c] = (row[2 * i], row[2 * i + 1])

    # 2. select list
    ident = (curate_cfg.get("identity_fields") or {}).get(name)
    ident_src = set(identity_source_cols(ident)) if ident else set()
    sel = [f"{typed_expr(c, kinds[c])} AS {q(c)}" for c in data_cols]
    for c in failing:  # keep the original text where the cast failed
        sel.append(f"CASE WHEN {typed_expr(c, kinds[c])} IS NULL THEN NULLIF(trim({q(c)}),'') END AS {q(c + '__raw')}")
    sel += META

    inner = f"SELECT {', '.join(q(c) for c in data_cols + META)} FROM raw.{q(name)}"
    body = f"SELECT {', '.join(sel)} FROM ({inner}) r"
    # dedupe on _row_hash, earliest ingest wins
    dedup = f"SELECT * FROM ({body}) QUALIFY row_number() OVER (PARTITION BY _row_hash ORDER BY _ingested_at) = 1"

    if ident:
        pre = ", ".join([f"{q(c)} AS raw_{c}" for c in sorted(ident_src) if c in data_cols] + ["*"])
        final = (f"SELECT * EXCLUDE ({', '.join('raw_' + c for c in sorted(ident_src) if c in data_cols)}), "
                 f"{', '.join(identity_select(name, ident))} FROM (SELECT {pre} FROM ({dedup}))")
        # raw_<col> must hold the typed (trimmed) text: identity cols are text kind so q(c) is already clean
    else:
        final = dedup

    con.execute(f"CREATE OR REPLACE TABLE curated.{q(name)} AS {final}")
    n_raw = con.execute(f"SELECT count(*) FROM raw.{q(name)}").fetchone()[0]
    n_cur = con.execute(f"SELECT count(*) FROM curated.{q(name)}").fetchone()[0]

    con.execute("DELETE FROM curated._cast_failures WHERE table_name = ?", [name])
    for c, (n, ex) in failing.items():
        con.execute("INSERT INTO curated._cast_failures VALUES (?,?,?,?,?, now())", [name, c, kinds[c], n, ex])
    con.execute("DELETE FROM curated._build_log WHERE table_name = ?", [name])
    con.execute("INSERT INTO curated._build_log VALUES (?,?,?,?,?,?,now())",
                [name, n_raw, n_cur, n_raw - n_cur, len(failing), round(time.time() - t0, 1)])
    return {"table": name, "raw": n_raw, "curated": n_cur, "dupes_removed": n_raw - n_cur,
            "cols_with_cast_failures": len(failing), "secs": round(time.time() - t0, 1)}


def build_current_views(con, curate_cfg: dict) -> None:
    for name, v in (curate_cfg.get("current_views") or {}).items():
        exists = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='curated' AND table_name=?",
                             [name]).fetchone()[0]
        if not exists:
            continue
        where = f"WHERE {v['where']}" if v.get("where") else ""
        con.execute(
            f"CREATE OR REPLACE TABLE curated.{q(name + '_current')} AS "
            f"SELECT * FROM curated.{q(name)} {where} "
            f"QUALIFY row_number() OVER (PARTITION BY {q(v['key'])} ORDER BY {v['order_by']}) = 1"
        )


def run(only: list[str] | None = None) -> list[dict]:
    root = config.maple_data_dir()
    schema = json.load(open(root / "schema" / "schema.json", encoding="utf-8"))
    curate_cfg = yaml.safe_load(open(config.CONFIG_DIR / "curate.yaml", encoding="utf-8"))
    con = connect()
    register_udfs(con)
    con.execute("CREATE TABLE IF NOT EXISTS curated._cast_failures (table_name VARCHAR, column_name VARCHAR, "
                "target_type VARCHAR, failed_rows BIGINT, example_raw VARCHAR, recorded_at TIMESTAMP)")
    con.execute("CREATE TABLE IF NOT EXISTS curated._build_log (table_name VARCHAR, raw_rows BIGINT, curated_rows BIGINT, "
                "dupes_removed BIGINT, cols_with_cast_failures INTEGER, secs DOUBLE, built_at TIMESTAMP)")
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='raw' "
        "AND table_name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY table_name").fetchall()]
    res = []
    for t in tables:
        if t == "transcript_json" or (only and t not in only):
            continue
        r = curate_table(con, t, schema, curate_cfg)
        res.append(r)
        print(f"{t:28s} {r['raw']:>11,} -> {r['curated']:>11,}  dupes {r['dupes_removed']:>8,}  "
              f"cast-fail cols {r['cols_with_cast_failures']:>3}  {r['secs']}s", flush=True)
    build_current_views(con, curate_cfg)
    con.close()
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="Build curated schema from raw")
    ap.add_argument("--only", nargs="*")
    run(ap.parse_args().only)


if __name__ == "__main__":
    main()
