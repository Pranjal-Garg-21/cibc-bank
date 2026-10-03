"""Run the Layer 1 stages that exist so far, in order, timing each one.

    python -m trustissues.factory.pipeline                 # everything
    python -m trustissues.factory.pipeline --from curate   # resume from a stage
    python -m trustissues.factory.pipeline --only ingest

Stages are re-runnable: ingest only reads new/changed files; curate rebuilds curated.* from raw.*.
Stages still to come are appended to STAGES as they are built.
"""
from __future__ import annotations

import argparse
import time

from trustissues.factory.curate import build_curated, text_controls
from trustissues.factory.gold import build_c360, publish, views
from trustissues.factory.identity import build as identity_build
from trustissues.factory.identity import evaluate as identity_evaluate
from trustissues.factory.ingest import dictionary, load_raw
from trustissues.factory.quality import dq_report


def _ingest() -> None:
    res = load_raw.run()
    blocked = [r["source"] for r in res if r["status"] == "BLOCKED"]
    if blocked:
        raise SystemExit(f"ingest blocked by schema drift: {blocked}")


STAGES = [
    ("ingest", _ingest),                   # source files -> raw.*
    ("dictionary", dictionary.main),       # data dictionary -> raw._dictionary, sensitivity labels (raw columns)
    ("curate", build_curated.run),         # raw.* -> curated.*
    ("quality", dq_report.run),            # dq flags, derived columns, reports/data_quality_report.md
    ("labels", dictionary.main),           # re-run so derived std_* columns inherit sensitivity labels
    ("text_controls", text_controls.main), # eligible, masked notes and transcripts
    ("identity", identity_build.run),      # curated.* -> identity.* -> gold_stage.customer_xref, review queue, relationships
    ("gold", build_c360.run),              # gold_stage.c360_account, case_customer, c360_customer
    ("views", views.main),                 # gold_stage consumer views (promises, safeguards, decisioning, timeline, buckets)
    ("publish", publish.run),              # contract gate; gold_stage -> gold only if every blocking rule passes
    ("identity_report", identity_evaluate.main),  # reports/identity_report.md
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the Layer 1 pipeline")
    ap.add_argument("--from", dest="start", choices=[n for n, _ in STAGES])
    ap.add_argument("--only", choices=[n for n, _ in STAGES])
    a = ap.parse_args()
    names = [n for n, _ in STAGES]
    todo = [(n, f) for n, f in STAGES if (a.only == n if a.only else (names.index(n) >= names.index(a.start) if a.start else True))]
    timings = []
    for name, fn in todo:
        print(f"\n=== {name} ===", flush=True)
        t0 = time.time()
        fn()
        timings.append((name, round(time.time() - t0, 1)))
    print("\nstage timings (s):", dict(timings), "| total:", round(sum(t for _, t in timings), 1))


if __name__ == "__main__":
    main()
