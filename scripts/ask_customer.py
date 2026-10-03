"""Developer tool: ask the customer path ONE question and print everything it did. Agents use
scripts/agent_dashboard.py instead (sign in once, type plain questions).

The case is found in the question itself; the flags are only for testing (another date, another user, more output).

    python scripts/ask_customer.py "Summarise the most recent recorded call on case CS-2026-614370: what did the customer say and agree to?"
    python scripts/ask_customer.py "What did the customer promise last?" --customer G-0200543 --as-of 2026-09-10
    python scripts/ask_customer.py "..." --case CS-2026-614370 --show-sources   # print every item the model was given
    python scripts/ask_customer.py "..." --case CS-2026-614370 --as-agent AG-0001   # check the access rule refuses

By default the question is asked as the agent the case is assigned to on the as-of date, so access passes
(or as a team lead when nobody holds the case).
Needs OPENROUTER_API_KEY (environment or .env) and the gold layer built (gold.customer_timeline, gold.case_customer).
"""
import argparse
import sys
import time
from datetime import date

from trustissues.ask.rag.customer_path import CustomerAccessDenied, case_assignee, resolve_case, run_customer
from trustissues.ask.rag.identify import identify
from trustissues.common import config, db
from trustissues.common.audit import AuditLog
from trustissues.common.llm import LLM


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    who = ap.add_mutually_exclusive_group()  # normally neither: the case ID is read from the question
    who.add_argument("--case", help="case ID; the customer is found through gold.case_customer")
    who.add_argument("--customer", help="golden customer ID")
    ap.add_argument("--as-of", default=config.AS_OF_DATE.isoformat())
    ap.add_argument("--as-agent", help="agent ID to ask as (default: the case's assignee on the as-of date)")
    ap.add_argument("--as-lead", help="team lead ID to ask as, instead of an agent")
    ap.add_argument("--lang", choices=["EN", "FR"], help="force the answer language (default: detected)")
    ap.add_argument("--budget", type=int, help="token budget (default from config)")
    ap.add_argument("--show-sources", action="store_true")
    a = ap.parse_args()

    if not (a.case or a.customer):
        tg = identify(a.question)
        a.case, a.customer = tg.case_id, tg.customer_id
        if not (a.case or a.customer):
            sys.exit("name a case (CS-2026-614370) or customer (G-0200543) in the question")
    as_of = date.fromisoformat(a.as_of)
    con = db.connect(read_only=False)
    gid = a.customer or resolve_case(con, a.case)
    if not gid:
        sys.exit(f"case {a.case} not found in gold.case_customer")
    role, uid = ("team_lead", a.as_lead) if a.as_lead else ("agent", a.as_agent or (a.case and case_assignee(con, a.case, as_of)))
    if not uid:  # nobody holds the case: unallocated work is visible to team leads
        role, uid = "team_lead", "TL-CLI"
        print("(nobody is assigned this case on the as-of date: asking as a team lead)")

    t = time.time()
    try:
        run = run_customer(a.question, gid, con=con, llm=LLM(audit=AuditLog(con)), as_of=as_of, user_role=role,
                           user_id=uid, case_id=a.case, language=a.lang, budget=a.budget)
    except CustomerAccessDenied as e:
        sys.exit(f"REFUSED   {e}  (asked as {role} {uid})")
    r = run.response
    print(f"\nQUESTION  {a.question}\nCUSTOMER  {gid}   CASE {a.case or '-'}   AS OF {as_of}   ASKED AS {role} {uid}   LANGUAGE {run.language}")
    print(f"\nREAD      {len(run.items)} of {run.total_items} items ({run.withheld} withheld)")
    for i in run.items:
        print(f"  {i.event_ts:%Y-%m-%d %H:%M}  {i.event_type:<10} {i.source_id:<14} {i.case_id or '':<16} {i.text[:70]!r}")
    if a.show_sources:
        print("\nTEXT SENT TO THE MODEL")
        for i in run.items:
            print(f"\n--- {i.source().label()}\n{i.text}")
    print(f"\nANSWER ({time.time() - t:.1f}s)  not_found={r.not_found}  confidence={r.confidence}\n  {r.answer}")
    print(f"\nCOVERAGE  {r.coverage}")
    for q in r.evidence:
        print(f'QUOTE     "{q}"')
    for c in r.citations:
        print(f"CITED     {c.kind} {c.source_id} {c.source_ts}")


if __name__ == "__main__":
    main()
