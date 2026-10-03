"""Run identity resolution end to end: curated.* -> identity.* -> gold.customer_xref (+ review queue, relationships).

    python -m trustissues.factory.identity.build              # all steps
    python -m trustissues.factory.identity.build --from cluster   # reuse the Splink scores and LLM verdicts

Steps, cheapest and surest first (plan §3.3). When unsure, records stay separate.
"""
from __future__ import annotations

import argparse
import time

from trustissues.common.db import connect
from trustissues.common.interfaces import apply_stubs

from . import cluster, nodes, relationships, review_queue, rules, second_opinion, splink_model

STEPS = [
    ("nodes", nodes.build),
    ("rules", rules.build),
    ("splink", splink_model.build),
    ("second_opinion", second_opinion.build),
    ("relationships", relationships.build_pairs),
    ("decisions", review_queue.load_decisions),
    ("cluster", cluster.build),
    ("review_queue", review_queue.build_queue),
    ("relationships_gold", relationships.build_gold),
]


def run(start: str | None = None) -> dict:
    con = connect()
    apply_stubs(con)  # gold.audit_log must exist before the LLM step writes to it
    review_queue.ensure_decisions_file()
    names = [n for n, _ in STEPS]
    out = {}
    for name, fn in STEPS:
        if start and names.index(name) < names.index(start):
            continue
        t0 = time.time()
        res = fn(con)
        out[name] = res
        print(f"[identity] {name:20s} {time.time() - t0:6.0f}s  {res}", flush=True)
    con.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Identity resolution")
    ap.add_argument("--from", dest="start", choices=[n for n, _ in STEPS])
    run(ap.parse_args().start)


if __name__ == "__main__":
    main()
