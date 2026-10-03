"""One command to check that P4 works end to end with the real model. Prints PASS / FAIL per check.

    python scripts/check_p4_live.py            # key, policy answer, customer answer, text features (~6 model calls)
    python scripts/check_p4_live.py --quick    # key and one policy answer only

Each check asks a question whose right answer is known (from the release's dev answers or labels) and tests the
reply for the facts that must be in it and the source it must cite. Costs a few cents at most.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import date

from trustissues.common import config
from trustissues.common.llm import LLM, LLMError, _load_dotenv

AS_OF = config.AS_OF_DATE
results: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:<42} {detail}", flush=True)


def check_key() -> bool:
    _load_dotenv()
    cfg = config.settings()["llm"]
    key = os.environ.get(cfg.get("api_key_env", "OPENROUTER_API_KEY"))
    if not key:
        report("API key present", False, "no key in the environment or .env")
        return False
    try:
        req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/key", headers={"Authorization": f"Bearer {key}"})
        d = json.load(urllib.request.urlopen(req, timeout=20))["data"]
        left = d.get("limit_remaining")
        ok = left is None or left > 0
        report("API key has credit", ok, f"used ${d.get('usage', 0):.3f}, limit {d.get('limit')}, remaining {left}")
        return ok
    except Exception as e:  # not every provider has this endpoint; the next checks will tell
        report("API key has credit", True, f"could not read the limit ({e}); trying a call")
        return True


def check_policy(llm, mode=None) -> None:
    from trustissues.ask.rag.answer import run_policy
    from trustissues.ask.rag.policy_search import PolicyIndex

    ix = PolicyIndex.load()
    cases = [  # (question, words the answer must contain, (doc, section) it must cite: any of)
        ("How many call attempts can we make on a mid-stage account in a week?", ["3"], {("POL-COLL-001", "§5.2")}),
        ("How long does the 3-month reduced payment programme last, and what share of the payment is reduced?",
         ["3", "50"], {("POL-COLL-004", "§4.2"), ("hardship_programs", "HP-LOAN-RED3")}),
        ("La liste nationale de numéros exclus bloque-t-elle les appels de recouvrement?", ["non"],
         {("POL-COLL-001", "§5.3"), ("FAQ-COLL-001", "Q2")}),
    ]
    for q, words, gold in cases:
        t = time.time()
        r = run_policy(q, ix, llm=llm, as_of=AS_OF, user_role="agent", mode=mode).response
        text = (r.answer or "").lower()
        has = all(w in text for w in words)
        cited = any((c.source_id, c.section) in gold for c in r.citations)
        report(f"policy: {q[:40]}", has and cited and not r.not_found,
               f"{time.time() - t:.0f}s, facts {'ok' if has else 'MISSING'}, cites {'ok' if cited else 'WRONG'}: "
               f"{(r.answer or '')[:70]!r}")


def check_customer(llm) -> None:
    from trustissues.ask.rag.customer_path import case_assignee, resolve_case, run_customer
    from trustissues.common.db import connect

    con = connect(read_only=True)
    cases = [  # BQ-016 and BQ-022, gold from labels/benchmark_dev_answers.csv
        ("CS-2026-614370", ["95"], "5010384"),
        ("CS-2026-855160", ["595"], "10650"),
    ]
    for case, words, transcript in cases:
        q = f"Summarise the most recent recorded call on case {case}: what did the customer say and agree to?"
        gid, agent = resolve_case(con, case), case_assignee(con, case, AS_OF)
        t = time.time()
        r = run_customer(q, gid, con=con, llm=llm, as_of=AS_OF, user_role="agent", user_id=agent, case_id=case).response
        has = all(w in (r.answer or "") for w in words)
        cited = any(c.source_id == transcript for c in r.citations)
        report(f"customer: {case}", has and cited and not r.not_found,
               f"{time.time() - t:.0f}s, facts {'ok' if has else 'MISSING'}, cites call {transcript} "
               f"{'ok' if cited else 'NO'}: {(r.answer or '')[:70]!r}")
    # access: another agent must be refused, without a model call
    from trustissues.ask.rag.customer_path import CustomerAccessDenied
    try:
        run_customer("anything", resolve_case(con, "CS-2026-614370"), con=con, llm=llm, as_of=AS_OF,
                     user_role="agent", user_id="AG-0000")
        report("customer: other agent refused", False, "was answered")
    except CustomerAccessDenied:
        report("customer: other agent refused", True, "refused, no model call")


def check_text_features(llm) -> None:
    from datetime import datetime

    from trustissues.features.text_extract import TextItem, extract_batch

    items = [
        TextItem("T-1", "note", "G-x", datetime(2026, 9, 1), "spoke w/ cust. forgot the due date. CB scheduled"),
        TextItem("T-2", "note", "G-x", datetime(2026, 9, 1),
                 "RPC w/ cust hours reduced at work since Apr. maybe $40 Friday, not sure. sent pmt link"),
        TextItem("T-3", "note", "G-x", datetime(2026, 9, 1), "spoke w/ cust. disputes the charge. says pmt not applied. dispute opened"),
    ]
    rows = {r["source_id"]: r for r in extract_batch(items, llm)}
    expect = {"T-1": ("forgot", False, False), "T-2": ("reduced_income", True, False), "T-3": ("dispute", False, True)}
    for sid, (reason, hardship, dispute) in expect.items():
        r = rows.get(sid)
        ok = bool(r) and r["stated_delay_reason"] == reason and bool(r["hardship_signal"]) == hardship \
            and bool(r["dispute_mentioned"]) == dispute
        got = (f"reason={r['stated_delay_reason']}, hardship={r['hardship_signal']}, dispute={r['dispute_mentioned']}, "
               f"intent={r['ptp_intent_strength']}, evidence={r['evidence_sentence']!r}") if r else "no row"
        report(f"text features: {sid}", ok, got)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--mode", help="policy retrieval mode to test (default: config policy_rag.mode)")
    a = ap.parse_args()
    if not check_key():
        sys.exit("\nThe key has no credit or is missing: raise the limit at openrouter.ai (Keys) and run again.")
    llm = LLM()
    try:
        check_policy(llm, a.mode)
        if not a.quick:
            check_customer(llm)
            check_text_features(llm)
    except LLMError as e:
        report("model reachable", False, str(e)[:200])
    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed" + (f"; failed: {', '.join(bad)}" if bad else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
