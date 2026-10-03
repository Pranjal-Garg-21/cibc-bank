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
    ap.add_argument("--llm", action="store_true", help="use the language model: policy answers and the SQL fallback")
    ap.add_argument("--only", nargs="*", help="question ids to run")
    a = ap.parse_args()
    qs = B.read_questions(maple_data_dir() / "data" / "benchmark_questions.csv")
    if a.split != "all":
        qs = [q for q in qs if q["split"] == a.split]
    if a.only:
        qs = [q for q in qs if q["question_id"] in a.only]
    kw = {}
    if a.llm:
        from trustissues.common.llm import LLM
        from trustissues.ask.rag.policy_search import PolicyIndex
        from trustissues.ask.rag_handler import make_rag_handler
        llm = LLM()
        kw = {"llm": llm, "rag": make_rag_handler(PolicyIndex.load(), llm)}
    rows = B.run(qs, database=Path(a.database), **kw)
    B.write_submission(rows, Path(a.out))
    print(f"wrote {len(rows)} rows to {a.out}")


if __name__ == "__main__":
    main()
