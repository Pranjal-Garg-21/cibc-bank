"""Run the benchmark through ask() and write the four-column submission CSV.

    python scripts/run_benchmark.py --database path/to/dev_ask_dev.duckdb --out submission.csv [--split dev]

The submission (released + 19:00 extras, model on, published warehouse):
    python scripts/run_benchmark.py --database warehouse/trustissues.duckdb --llm \
        --extra eval/benchmark_questions_extra.csv --out submission/benchmark_answers.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

from trustissues.common.config import maple_data_dir
from trustissues.ask import benchmark as B


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", required=True, help="the warehouse (or an *_ask_dev.duckdb copy) with gold.semantic_*, read-only")
    ap.add_argument("--out", default="submission.csv")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="all")
    ap.add_argument("--llm", action="store_true", help="use the language model: policy answers and the SQL fallback")
    ap.add_argument("--no-cache", action="store_true", help="bypass the LLM response cache: every model call is made fresh")
    ap.add_argument("--only", nargs="*", help="question ids to run")
    ap.add_argument("--extra", nargs="*", default=[], help="more question files in the same format (the 19:00 4 Oct release)")
    a = ap.parse_args()
    qs = B.read_questions(maple_data_dir() / "data" / "benchmark_questions.csv")
    for extra in a.extra:
        qs += B.read_questions(Path(extra))
    if a.split != "all":
        qs = [q for q in qs if q["split"] == a.split]
    if a.only:
        qs = [q for q in qs if q["question_id"] in a.only]
    # read-only: case questions are resolved on it (asked as the case's agent) and the RAG paths read through it
    con = duckdb.connect(str(Path(a.database)), read_only=True)
    kw = {}
    if a.llm:
        from trustissues.common.llm import LLM
        from trustissues.ask.rag.policy_search import PolicyIndex
        from trustissues.ask.rag_handler import make_rag_handler
        llm = LLM()
        if a.no_cache:
            llm.cache = None
        kw = {"llm": llm, "rag": make_rag_handler(PolicyIndex.load(), llm, con=con)}
    try:
        rows = B.run(qs, con=con, **kw)
    finally:
        con.close()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    B.write_submission(rows, Path(a.out))
    print(f"wrote {len(rows)} rows to {a.out}")


if __name__ == "__main__":
    main()
