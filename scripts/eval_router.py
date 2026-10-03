"""Score the question router on labelled paraphrases (eval/router_cases.csv). No data or model needed.

Expectations: a path name (metric, rag_corpus, rag_customer, sql, drivers), `refused` (refusal gate or injection
screen), or `allowed` (must simply not be refused). A metric case also checks the metric id.

    python scripts/eval_router.py            # prints accuracy by category and every miss
    python scripts/eval_router.py --min 0.9  # exit 1 if overall accuracy falls below the bar

A change to the refusal gate, injection screen, catalogue or router must not lower this number.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict

from trustissues.common.config import REPO_ROOT
from trustissues.ask.service import classify

CASES = REPO_ROOT / "eval" / "router_cases.csv"
HELDOUT = REPO_ROOT / "eval" / "router_heldout.csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=0.0)
    ap.add_argument("--llm", action="store_true", help="let the model place questions the rules could not")
    ap.add_argument("--heldout", action="store_true", help="score eval/router_heldout.csv (never tune against it)")
    a = ap.parse_args()
    llm = None
    if a.llm:
        from trustissues.common.llm import LLM
        llm = LLM()
    rows = list(csv.DictReader(open(HELDOUT if a.heldout else CASES, encoding="utf-8")))
    by: dict[str, list[bool]] = defaultdict(list)
    misses = []
    for r in rows:
        outcome, metric = classify(r["question"], llm)
        if r["expect"] == "allowed":  # only checks that a legitimate question is not wrongly refused
            ok = outcome != "refused"
        else:
            ok = outcome == r["expect"] and (not r["metric"] or metric == r["metric"])
        by[r["expect"]].append(ok)
        if not ok:
            misses.append((r["expect"], r["metric"], outcome, metric, r["question"]))
    total = sum(len(v) for v in by.values())
    good = sum(sum(v) for v in by.values())
    for k, v in sorted(by.items()):
        print(f"{k:12s} {sum(v):3d}/{len(v):3d}")
    print(f"{'OVERALL':12s} {good:3d}/{total:3d} = {good / total:.1%}")
    for exp, m, got, gm, q in misses:
        print(f"  MISS expected {exp}/{m or '-'} got {got}/{gm or '-'}: {q}")
    return 0 if good / total >= a.min else 1


if __name__ == "__main__":
    sys.exit(main())
