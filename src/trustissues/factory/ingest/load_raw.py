"""Load every source table into raw.<table> exactly as received (all columns as text).

Dynamic by design: sources are discovered from the data folder, not hard-coded. Registered
sources (in schema.json) get a drift check; unregistered ones (a new extract dropped into
data/) are loaded too and reported so a mapping can be approved. Re-running only reads files
that are new or changed, and skips rows already loaded in an earlier batch (by _row_hash).

Metadata added to each row: _ingested_at, _source_file, _batch_id, _row_hash.
Exact duplicates *within* a batch are kept; curated dedupes them.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from trustissues.common import config
from trustissues.common.db import connect

from . import manifest, schema_drift

META = ["_ingested_at", "_source_file", "_batch_id", "_row_hash"]
BS = chr(92)  # backslash, for normalising Windows paths inside SQL


@dataclass
class Source:
    name: str
    kind: str  # csv | parquet
    files: list[manifest.FileInfo]
    expected: list[str] | None


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def lit(path) -> str:
    return "'" + str(path).replace(BS, "/").replace("'", "''") + "'"


def _norm(col: str) -> str:
    return f"replace({col}, '{BS}', '/')"


def _cols(registered: dict, name: str):
    t = registered.get(name)
    return [c["name"] for c in t["columns"]] if t else None


def discover(root, registered: dict) -> list[Source]:
    data = root / "data"
    out: list[Source] = []
    for p in sorted(data.glob("*.csv")):
        out.append(Source(p.stem, "csv", [manifest.stat(p, root)], _cols(registered, p.stem)))
    for d in sorted(x for x in data.iterdir() if x.is_dir()):
        parts = sorted(d.glob("year=*/*.parquet"))
        if parts:
            out.append(Source(d.name, "parquet", [manifest.stat(p, root) for p in parts], _cols(registered, d.name)))
    return out


def _reader(src: Source, files: list[manifest.FileInfo]) -> str:
    paths = "[" + ",".join(lit(f.path) for f in files) + "]"
    if src.kind == "csv":
        return f"read_csv({paths}, header=true, all_varchar=true, union_by_name=true, sample_size=-1, filename=true)"
    return f"read_parquet({paths}, hive_partitioning=true, union_by_name=true, filename=true)"


def _columns(con, reader: str) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {reader}").fetchall() if r[0] != "filename"]


def load_source(con, src: Source, batch_id: str, allow_missing) -> dict:
    changed = manifest.changed_files(con, src.name, src.files)
    if not changed:
        return {"source": src.name, "status": "unchanged", "rows": 0}

    reader = _reader(src, changed)
    cols = _columns(con, reader)
    # the hive partition column (year=2026/) comes from the folder name, not from the data
    data_cols = [c for c in cols if not (src.kind == "parquet" and c == "year")]
    drift = schema_drift.detect(src.name, src.expected, data_cols, allow_missing)
    schema_drift.log(con, batch_id, drift)
    schema_drift.enforce(drift)

    select_cols = ", ".join(f"CAST({q(c)} AS VARCHAR) AS {q(c)}" for c in cols)
    hash_expr = (
        "md5(concat_ws(chr(31), "
        + ", ".join(f"coalesce(CAST({q(c)} AS VARCHAR), chr(0))" for c in cols)
        + "))"
    )
    tbl = f"raw.{q(src.name)}"
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='raw' AND table_name=?", [src.name]
    ).fetchone()[0]

    staged = f"""
        SELECT {select_cols},
               now() AS _ingested_at,
               {_norm('filename')} AS _source_file,
               '{batch_id}' AS _batch_id,
               {hash_expr} AS _row_hash
        FROM {reader}"""
    if not exists:
        con.execute(f"CREATE TABLE {tbl} AS {staged}")
        added = con.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
    else:
        existing = [r[0] for r in con.execute(f"DESCRIBE {tbl}").fetchall()]
        for c in cols:  # a column added by a later extract widens the table
            if c not in existing:
                con.execute(f"ALTER TABLE {tbl} ADD COLUMN {q(c)} VARCHAR")
        existing = [r[0] for r in con.execute(f"DESCRIBE {tbl}").fetchall()]
        con.execute("CREATE OR REPLACE TEMP TABLE _stage AS " + staged)
        before = con.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
        out_cols = ", ".join(q(c) for c in existing)
        sel = ", ".join(q(c) if (c in cols or c in META) else "NULL" for c in existing)
        con.execute(
            f"INSERT INTO {tbl} ({out_cols}) SELECT {sel} FROM _stage s "
            f"WHERE NOT EXISTS (SELECT 1 FROM {tbl} t WHERE t._row_hash = s._row_hash)"
        )
        added = con.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0] - before

    counts = dict(con.execute(f"SELECT _source_file, count(*) FROM {tbl} GROUP BY 1").fetchall())
    for f in changed:
        n = next((v for k, v in counts.items() if k.endswith(f.rel) or k.endswith(f.path.as_posix())), 0)
        manifest.record(con, src.name, f, n, cols, batch_id)
    return {"source": src.name, "status": "loaded", "rows": added, "files": len(changed),
            "unexpected": drift.unexpected}


def load_transcripts(con, root, batch_id: str) -> dict:
    """Transcript JSON files, one row per file, content kept verbatim as text."""
    glob = config.settings()["ingest"]["transcript_glob"]
    pattern = lit(root / glob)
    con.execute(
        "CREATE TABLE IF NOT EXISTS raw.transcript_json (transcript_id VARCHAR, content VARCHAR, "
        "_ingested_at TIMESTAMP, _source_file VARCHAR, _batch_id VARCHAR, _row_hash VARCHAR)"
    )
    before = con.execute("SELECT count(*) FROM raw.transcript_json").fetchone()[0]
    con.execute(
        f"""INSERT INTO raw.transcript_json
        SELECT regexp_extract({_norm('filename')}, '([^/]+)[.]json$', 1), content, now(),
               {_norm('filename')}, '{batch_id}', md5(content)
        FROM read_text({pattern}) s
        WHERE NOT EXISTS (SELECT 1 FROM raw.transcript_json t WHERE t._source_file = {_norm('s.filename')})"""
    )
    added = con.execute("SELECT count(*) FROM raw.transcript_json").fetchone()[0] - before
    return {"source": "transcript_json", "status": "loaded" if added else "unchanged", "rows": added}


def run(only: list[str] | None = None, with_transcripts: bool = True) -> list[dict]:
    root = config.maple_data_dir()
    registered = json.load(open(root / "schema" / "schema.json", encoding="utf-8"))
    allow = config.settings()["ingest"].get("allow_missing_columns") or {}
    batch_id = "B" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    con = connect()
    results = []
    for src in discover(root, registered):
        if only and src.name not in only:
            continue
        t0 = time.time()
        try:
            r = load_source(con, src, batch_id, allow.get(src.name, ()))
        except schema_drift.SchemaDriftError as e:
            r = {"source": src.name, "status": "BLOCKED", "rows": 0, "error": str(e)}
        r["secs"] = round(time.time() - t0, 1)
        r["registered"] = src.expected is not None
        results.append(r)
        print(
            f"{r['source']:28s} {r['status']:9s} +{r['rows']:>10,} rows {r['secs']:>6}s"
            + (f"  new cols: {r['unexpected']}" if r.get("unexpected") else "")
            + (f"  {r['error']}" if r.get("error") else ""),
            flush=True,
        )
    if with_transcripts and not only:
        results.append(load_transcripts(con, root, batch_id))
        print(f"{'transcript_json':28s} +{results[-1]['rows']:,} rows")
    con.close()
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Load source files into DuckDB raw schema")
    ap.add_argument("--only", nargs="*", help="limit to these source names")
    ap.add_argument("--no-transcripts", action="store_true")
    a = ap.parse_args()
    res = run(a.only, not a.no_transcripts)
    if any(r["status"] == "BLOCKED" for r in res):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
