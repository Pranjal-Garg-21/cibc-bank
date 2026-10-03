"""Ask the policy path one question from the command line and see exactly what happens.

    python scripts/ask_policy.py "Does the Do Not Call List stop collection calls?"
    python scripts/ask_policy.py "Quelles sont les heures permises pour appeler?"      # answers in French: language is detected
    python scripts/ask_policy.py "What was the reduced payment in 2025?" --as-of 2025-06-01
    python scripts/ask_policy.py "..." --show-sources   # also print the full text the model was given, to verify the answer
    python scripts/ask_policy.py "..." --no-hardship     # skip the hardship table (needed while another process holds the database)

Needs OPENROUTER_API_KEY (environment or .env) and warehouse/rag/policy_chunks.jsonl
(python -m trustissues.ask.rag.policy_ingest).
"""
import argparse
import time
from datetime import date

from trustissues.ask.rag.answer import run_policy
from trustissues.ask.rag.policy_search import PolicyIndex
from trustissues.common import config
from trustissues.common.llm import LLM


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--lang", choices=["EN", "FR"], help="force the answer language (default: detected from the question)")
    ap.add_argument("--province", help="province code, e.g. QC (default: detected from the question)")
    ap.add_argument("--as-of", default=config.AS_OF_DATE.isoformat(), help="date the rule must apply on")
    ap.add_argument("--no-hardship", action="store_true", help="skip the hardship table (database busy)")
    ap.add_argument("--show-sources", action="store_true", help="print the full text of every section sent to the model")
    a = ap.parse_args()

    ix, llm = PolicyIndex.load(), LLM()
    t = time.time()
    run = run_policy(a.question, ix, llm=llm, as_of=date.fromisoformat(a.as_of), user_role="agent",
                     language=a.lang, province=a.province, with_hardship=not a.no_hardship)
    found, r = run.result, run.response
    print(f"\nQUESTION  {a.question}\nAS OF     {found.as_of_date}   LANGUAGE {run.language}   PROVINCE {run.province or 'none'}")
    if run.query != a.question:
        print(f"SEARCHED  {run.query}")
    print(f"\nRETRIEVED ({found.candidates_considered} sections were eligible on that date)")
    for s in found.sections:
        print(f"  {s.score:6.1f}  {s.doc_id} v{s.version} {s.section_id} [{s.language}]" + ("  (always included: province rule)" if s.pinned else ""))
    for h in found.hardship_rows:
        print(f"          hardship_programs {h['program_id']} v{h['version']}")
    if a.show_sources:
        print("\nTEXT SENT TO THE MODEL")
        for s in found.sections:
            print(f"\n--- {s.doc_id} v{s.version} {s.section_id} [{s.language}]\n{s.text}")
    print(f"\nANSWER ({time.time() - t:.1f}s)  not_found={r.not_found}  confidence={r.confidence}\n  {r.answer}")
    for q in r.evidence:
        print(f'\nQUOTE     "{q}"')
    for c in r.citations:
        print(f"CITED     {c.source_id} v{c.version} {c.section}")


if __name__ == "__main__":
    main()
