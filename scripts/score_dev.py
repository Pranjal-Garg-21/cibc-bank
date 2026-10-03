"""Development scorer (an approximation, NOT the official grader). Scores a submission CSV against the dev gold.

    python scripts/score_dev.py submission.csv
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from trustissues.common.config import maple_data_dir
from trustissues.ask import benchmark as B


def main() -> None:
    root = maple_data_dir()
    qs = B.read_questions(root / "data" / "benchmark_questions.csv")
    gold = B.read_gold(root / "labels" / "benchmark_dev_answers.csv")
    sub = list(csv.DictReader(open(sys.argv[1], encoding="utf-8", newline="")))
    results = B.score(qs, gold, sub)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        print(f"{r.question_id:8s} {r.status:22s} " + "; ".join(r.detail))
    print("\n" + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
