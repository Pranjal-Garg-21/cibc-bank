"""The morning run: Layer 1, then the next-best-action queue and the reports that check it.

    python scripts/nightly.py                    # everything
    python scripts/nightly.py --skip-pipeline    # P3 only, on the warehouse as it is
    python scripts/nightly.py --from queue       # resume from a stage
    python scripts/nightly.py --dry-run          # print the commands

Stages, in order (each runs as its own process, so connections and memory do not carry over):
    pipeline       Layer 1: raw -> curated -> quality (factory/pipeline.py, P1)
    text_features  P4's LLM text features for open-case customers, newest 20,000 not yet extracted
                   (features/text_extract.py --scope open_cases). Without an LLM key it stops itself at once
                   and the run goes on; the queue then works from the features already in gold.text_features
    queue          score every open case as of AS_OF_DATE and write gold.nba_decisions (nba/queue.py --write-gold)
    fairness       reports/fairness_report.md (nba/fairness.py)
    monitoring     reports/monitoring.md (governance/monitoring.py)

The queue writes to the warehouse, so nothing else may hold it open (stop the app: streamlit holds the write lock).
Retraining is not nightly: `make train` rebuilds the training sets, features and both models. Each stage's time is
printed and appended to warehouse/p3/nightly.log; the run stops at the first stage that fails.
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import time
from datetime import datetime

from trustissues.common import config

STAGES = [
    ("pipeline", ["trustissues.factory.pipeline"]),
    ("text_features", ["trustissues.features.text_extract", "--scope", "open_cases", "--limit", "20000"]),
    ("queue", ["trustissues.nba.queue", "--write-gold"]),
    ("fairness", ["trustissues.nba.fairness"]),
    ("monitoring", ["trustissues.governance.monitoring"]),
]
OPTIONAL = {"text_features"}  # skipped if the module is missing (a checkout without P4's work)


def plan(start: str | None = None, only: str | None = None, skip_pipeline: bool = False) -> list[tuple[str, list[str]]]:
    names = [n for n, _ in STAGES]
    todo = []
    for name, cmd in STAGES:
        if only and name != only:
            continue
        if start and names.index(name) < names.index(start):
            continue
        if skip_pipeline and name == "pipeline":
            continue
        if name in OPTIONAL and importlib.util.find_spec(cmd[0]) is None:
            print(f"skip {name}: {cmd[0]} not built yet")
            continue
        todo.append((name, [sys.executable, "-m", *cmd]))
    return todo


def main(argv: list[str] | None = None) -> int:
    names = [n for n, _ in STAGES]
    ap = argparse.ArgumentParser(description="The morning run")
    ap.add_argument("--from", dest="start", choices=names)
    ap.add_argument("--only", choices=names)
    ap.add_argument("--skip-pipeline", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    todo = plan(a.start, a.only, a.skip_pipeline)
    if a.dry_run:
        for name, cmd in todo:
            print(f"{name}: {' '.join(cmd[1:])}")
        return 0
    log = config.p3_dir() / "nightly.log"
    timings = []
    for name, cmd in todo:
        print(f"\n=== {name} ===", flush=True)
        t0 = time.time()
        code = subprocess.run(cmd, cwd=config.REPO_ROOT).returncode
        timings.append((name, round(time.time() - t0, 1), code))
        if code:
            break
    line = (f"{datetime.now():%Y-%m-%d %H:%M} as_of={config.AS_OF_DATE} "
            + " ".join(f"{n}={t}s" + ("" if c == 0 else f"(exit {c})") for n, t, c in timings))
    with log.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"\n{line}\nlogged to {log}")
    return next((c for _, _, c in timings if c), 0)


if __name__ == "__main__":
    raise SystemExit(main())
