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


def date_fallback(b: str) -> str:
    """Recover a date written in a non-ISO format, only when the reading is unambiguous.

    YYYYMMDD is always unambiguous. For d/m/Y vs m/d/Y the part greater than 12 must be the day;
    when both parts are <= 12 the value is genuinely ambiguous and stays NULL (its text is kept in <col>__raw).
    """
    p1, p2 = f"TRY_CAST(split_part({b}, '/', 1) AS INT)", f"TRY_CAST(split_part({b}, '/', 2) AS INT)"
    return (
        f"CASE WHEN regexp_matches({b}, '^[0-9]{{8}}$') THEN TRY_CAST(try_strptime({b}, '%Y%m%d') AS DATE) "
        f"WHEN regexp_matches({b}, '^[0-9]{{1,2}}/[0-9]{{1,2}}/[0-9]{{4}}$') THEN "
        f"CASE WHEN {p1} > 12 AND {p2} <= 12 THEN TRY_CAST(try_strptime({b}, '%d/%m/%Y') AS DATE) "
        f"WHEN {p2} > 12 AND {p1} <= 12 THEN TRY_CAST(try_strptime({b}, '%m/%d/%Y') AS DATE) END END"
    )


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
        return f"COALESCE(TRY_CAST({blank} AS DATE), {date_fallback(blank)})"
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
        ("std_phone", S.parse_phone, ["VARCHAR"],
         "STRUCT(phone_e164 VARCHAR, phone_masked BOOLEAN, phone_prefix VARCHAR, phone_suffix VARCHAR)"),
        ("std_postal", S.parse_postal, ["VARCHAR"], "STRUCT(postal VARCHAR, fsa VARCHAR)"),
        ("std_employer", S.normalise_employer, ["VARCHAR"], "VARCHAR"),
    ]
    for name, fn, params, ret in reg:
        con.create_function(name, fn, [t(p) for p in params], t(ret))

    dob_t = t("STRUCT(dob_iso VARCHAR, dob_alt_iso VARCHAR, dob_ambiguous BOOLEAN, dob_was_ambiguous BOOLEAN, dob_basis VARCHAR)")
    for conv in (None, "day_first", "month_first"):  # one function per convention actually used
        con.create_function(f"std_dob_{conv or 'none'}", (lambda c: (lambda raw: S.parse_dob(raw, c)))(conv), [t("VARCHAR")], dob_t)

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


def date_convention(con, table: str, col: str) -> dict:
    """Evidence for which part of d/m/Y is the day in a raw date column, from the dates that can only be read one way."""
    pat = "^[0-9]{1,2}[/.-][0-9]{1,2}[/.-][0-9]{4}$"
    a = f"try_cast(regexp_extract({q(col)}, '^([0-9]{{1,2}})[/.-]', 1) AS INT)"
    b = f"try_cast(regexp_extract({q(col)}, '^[0-9]{{1,2}}[/.-]([0-9]{{1,2}})[/.-]', 1) AS INT)"
    first, second, ambiguous = con.execute(
        f"SELECT count(*) FILTER (WHERE {a} > 12), count(*) FILTER (WHERE {b} > 12), "
        f"count(*) FILTER (WHERE {a} <= 12 AND {b} <= 12 AND {a} <> {b}) "
        f"FROM raw.{q(table)} WHERE regexp_matches({q(col)}, '{pat}')").fetchone()
    return {"first_over_12": first, "second_over_12": second, "ambiguous_rows": ambiguous,
            "decision": S.infer_date_convention(first, second)}


def identity_select(table: str, cfg: dict, dob_convention: str | None = None) -> list[str]:
    """SQL select items that add std_* columns for one source."""
    items: list[str] = []
    n = cfg.get("name")
    if n:
        fn = f"std_name_{n.get('order', 'auto')}_{n.get('caps_default') or 'none'}"
        c = f"{fn}(raw_{n['col']})"
        for f in ("first", "middle_initials", "last", "first_is_initial", "name_key"):
            items.append(f"{c}.{f} AS std_name_{f}")
    if cfg.get("dob"):
        c = f"std_dob_{dob_convention or 'none'}(raw_{cfg['dob']})"
        clean = f"raw_{cfg['dob_clean']}" if cfg.get("dob_clean") else None
        if clean:  # the source's own cleaned date wins; validated against every unambiguous raw value (see curation.md)
            items += [f"coalesce({clean}, {c}.dob_iso) AS std_dob",
                      f"CASE WHEN {clean} IS NOT NULL THEN NULL ELSE {c}.dob_alt_iso END AS std_dob_alt",
                      f"CASE WHEN {clean} IS NOT NULL THEN false ELSE {c}.dob_ambiguous END AS std_dob_ambiguous",
                      f"{c}.dob_was_ambiguous AS std_dob_was_ambiguous",
                      f"CASE WHEN {clean} IS NOT NULL THEN 'source_clean_column' ELSE {c}.dob_basis END AS std_dob_basis"]
        else:
            items += [f"{c}.dob_iso AS std_dob", f"{c}.dob_alt_iso AS std_dob_alt", f"{c}.dob_ambiguous AS std_dob_ambiguous",
                      f"{c}.dob_was_ambiguous AS std_dob_was_ambiguous", f"{c}.dob_basis AS std_dob_basis"]
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
    for k in ("dob", "dob_clean", "postal", "employer", "postal_from_address"):
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
    recovered: dict[str, tuple[int, str | None]] = {}
    if typed_cols:
        aggs = []
        for c in typed_cols:
            nonblank = f"NULLIF(trim({q(c)}),'') IS NOT NULL"
            aggs.append(f"count(*) FILTER (WHERE {nonblank} AND {typed_expr(c, kinds[c])} IS NULL)")
            aggs.append(f"any_value({q(c)}) FILTER (WHERE {nonblank} AND {typed_expr(c, kinds[c])} IS NULL)")
            if kinds[c] == "date":  # parsed only thanks to the non-ISO fallback
                rec = f"{nonblank} AND TRY_CAST(NULLIF(trim({q(c)}),'') AS DATE) IS NULL AND {typed_expr(c, kinds[c])} IS NOT NULL"
                aggs.append(f"count(*) FILTER (WHERE {rec})")
                aggs.append(f"any_value({q(c)}) FILTER (WHERE {rec})")
            else:
                aggs.extend(["0", "NULL"])
        row = con.execute(f"SELECT {', '.join(aggs)} FROM raw.{q(name)}").fetchone()
        for i, c in enumerate(typed_cols):
            if row[4 * i]:
                failing[c] = (row[4 * i], row[4 * i + 1])
            if row[4 * i + 2]:
                recovered[c] = (row[4 * i + 2], row[4 * i + 3])

    # 2. select list
    ident = (curate_cfg.get("identity_fields") or {}).get(name)
    ident_src = set(identity_source_cols(ident)) if ident else set()
    dob_conv = None
    con.execute("CREATE TABLE IF NOT EXISTS curated._date_conventions (table_name VARCHAR, column_name VARCHAR, "
                "first_over_12 BIGINT, second_over_12 BIGINT, ambiguous_rows BIGINT, decision VARCHAR, recorded_at TIMESTAMP)")
    con.execute("DELETE FROM curated._date_conventions WHERE table_name = ?", [name])
    if ident and ident.get("dob") and ident["dob"] in data_cols:
        ev = date_convention(con, name, ident["dob"])
        dob_conv = ev["decision"]
        con.execute("INSERT INTO curated._date_conventions VALUES (?,?,?,?,?,?, now())",
                    [name, ident["dob"], ev["first_over_12"], ev["second_over_12"], ev["ambiguous_rows"], dob_conv or "mixed: not inferred"])
    sel = [f"{typed_expr(c, kinds[c])} AS {q(c)}" for c in data_cols]
    for c in failing:  # keep the original text where the cast failed
        sel.append(f"CASE WHEN {typed_expr(c, kinds[c])} IS NULL THEN NULLIF(trim({q(c)}),'') END AS {q(c + '__raw')}")
    sel += META

    inner = f"SELECT {', '.join(q(c) for c in data_cols + META)} FROM raw.{q(name)}"
    body = f"SELECT {', '.join(sel)} FROM ({inner}) r"
    # dedupe on _row_hash, earliest ingest wins
    dedup = f"SELECT * FROM ({body}) QUALIFY row_number() OVER (PARTITION BY _row_hash ORDER BY _ingested_at) = 1"

    if ident:
        # the standardise functions take text; a source column may be typed (external.subject_dob_reported is a DATE)
        pre = ", ".join([f"CAST({q(c)} AS VARCHAR) AS raw_{c}" for c in sorted(ident_src) if c in data_cols] + ["*"])
        final = (f"SELECT * EXCLUDE ({', '.join('raw_' + c for c in sorted(ident_src) if c in data_cols)}), "
                 f"{', '.join(identity_select(name, ident, dob_conv))} FROM (SELECT {pre} FROM ({dedup}))")
        # raw_<col> must hold the typed (trimmed) text: identity cols are text kind so q(c) is already clean
    else:
        final = dedup

    con.execute(f"CREATE OR REPLACE TABLE curated.{q(name)} AS {final}")
    n_raw = con.execute(f"SELECT count(*) FROM raw.{q(name)}").fetchone()[0]
    n_cur = con.execute(f"SELECT count(*) FROM curated.{q(name)}").fetchone()[0]

    con.execute("CREATE TABLE IF NOT EXISTS curated._format_recoveries (table_name VARCHAR, column_name VARCHAR, "
                "recovered_rows BIGINT, example_raw VARCHAR, recorded_at TIMESTAMP)")
    con.execute("DELETE FROM curated._format_recoveries WHERE table_name = ?", [name])
    for c, (n, ex) in recovered.items():
        con.execute("INSERT INTO curated._format_recoveries VALUES (?,?,?,?, now())", [name, c, n, ex])
    con.execute("DELETE FROM curated._cast_failures WHERE table_name = ?", [name])
    for c, (n, ex) in failing.items():
        con.execute("INSERT INTO curated._cast_failures VALUES (?,?,?,?,?, now())", [name, c, kinds[c], n, ex])
    con.execute("DELETE FROM curated._build_log WHERE table_name = ?", [name])
    con.execute("INSERT INTO curated._build_log VALUES (?,?,?,?,?,?,now())",
                [name, n_raw, n_cur, n_raw - n_cur, len(failing), round(time.time() - t0, 1)])
    return {"table": name, "raw": n_raw, "curated": n_cur, "dupes_removed": n_raw - n_cur,
            "cols_with_cast_failures": len(failing), "cols_with_recovered_formats": len(recovered),
            "secs": round(time.time() - t0, 1)}


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
              f"cast-fail cols {r['cols_with_cast_failures']:>3}  recovered-format cols {r['cols_with_recovered_formats']:>2}  {r['secs']}s", flush=True)
    build_current_views(con, curate_cfg)
    con.close()
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="Build curated schema from raw")
    ap.add_argument("--only", nargs="*")
    run(ap.parse_args().only)


if __name__ == "__main__":
    main()
