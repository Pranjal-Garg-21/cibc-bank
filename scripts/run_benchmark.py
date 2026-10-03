"""Run the benchmark through ask() and write the four-column submission CSV.

    python scripts/run_benchmark.py --database path/to/dev_ask_dev.duckdb --out submission.csv [--split dev]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from trustissues.common.config import maple_data_dir
from trustissues.ask import benchmark as B


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", required=True, help="DuckDB file holding the gold.semantic_* views (read-only)")
    ap.add_argument("--out", default="submission.csv")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="all")
    a = ap.parse_args()
    qs = B.read_questions(maple_data_dir() / "data" / "benchmark_questions.csv")
    if a.split != "all":
        qs = [q for q in qs if q["split"] == a.split]
    rows = B.run(qs, database=Path(a.database))
    B.write_submission(rows, Path(a.out))
    print(f"wrote {len(rows)} rows to {a.out}")


if __name__ == "__main__":
    main()
