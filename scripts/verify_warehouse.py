"""Check that a freshly built warehouse is correct. Exit code 0 = all checks pass.

    python scripts/verify_warehouse.py

1. raw.<table> row counts equal the counts the release itself publishes (schema.json)
2. raw.transcript_json has one row per transcript file
3. no blocking schema drift was recorded
4. if curated exists: curated rows = raw rows minus exact duplicates, for every table
"""
import json
import sys

from trustissues.common import config
from trustissues.common.db import connect


def main() -> int:
    root = config.maple_data_dir()
    schema = json.load(open(root / "schema" / "schema.json", encoding="utf-8"))
    con = connect(read_only=True)
    failures = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal failures
        failures += not ok
        print(("PASS  " if ok else "FAIL  ") + msg)

    for t, meta in schema.items():
        n = con.execute(f'SELECT count(*) FROM raw."{t}"').fetchone()[0]
        check(n == meta["rows"], f"raw.{t}: {n:,} rows (release says {meta['rows']:,})")

    files = len(list((root / "files" / "transcripts").glob("*/*/*.json")))
    n = con.execute("SELECT count(*) FROM raw.transcript_json").fetchone()[0]
    check(n == files, f"raw.transcript_json: {n:,} rows for {files:,} files")

    blocking = con.execute("SELECT count(*) FROM raw._schema_drift WHERE blocking").fetchone()[0]
    check(blocking == 0, f"blocking schema drift events: {blocking}")

    has_curated = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='curated' "
                              "AND table_name='_build_log'").fetchone()[0]
    if has_curated:
        for t, raw_n, cur_n, dup in con.execute(
                "SELECT table_name, raw_rows, curated_rows, dupes_removed FROM curated._build_log ORDER BY 1").fetchall():
            actual = con.execute(f'SELECT count(*) FROM curated."{t}"').fetchone()[0]
            check(actual == cur_n and raw_n - cur_n == dup, f"curated.{t}: {actual:,} rows ({dup:,} duplicates removed)")
    else:
        print("SKIP  curated checks (curated has not been built)")

    print(f"\n{'ALL CHECKS PASSED' if not failures else str(failures) + ' CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
