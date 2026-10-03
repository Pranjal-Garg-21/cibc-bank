"""Compare the policy retrieval modes on questions whose correct section is known.

    python scripts/compare_policy_modes.py               # retrieval only: no LLM, no cost
    python scripts/compare_policy_modes.py --answers     # also answer each question in each mode (needs the API key)

Retrieval score: where the first correct section lands in the sections given to the model (rank 1 = top),
hit@5 (a correct section is among the 5 sections the model reads) and mean reciprocal rank. whole_corpus gives the
model every section in force, so it always "hits"; the question for it is whether the answer is as good when the model
reads ~4K tokens instead of 5 sections, which --answers shows.
Writes reports/policy_retrieval_comparison.md.
"""
from __future__ import annotations

import argparse
import time
from datetime import date

from trustissues.ask.rag import retrievers
from trustissues.ask.rag.policy_search import PolicyIndex
from trustissues.common import config

# (question id, question, acceptable (doc, section) pairs, province). BQ-* are the release's benchmark questions
# (dev gold sources from labels/benchmark_dev_answers.csv; test ones checked by hand); P-* are paraphrases written
# to avoid the documents' own words, which is where keyword search fails.
PROBES = [
    ("BQ-002", "How many call attempts can we make on a mid-stage account in a week?", {("POL-COLL-001", "§5.2")}, None),
    ("BQ-005", "Does the National Do Not Call List stop collection calls?", {("POL-COLL-001", "§5.3"), ("FAQ-COLL-001", "Q2")}, None),
    ("BQ-019", "A customer says their hours were cut at work. What must the agent do before asking for payment?",
     {("POL-COLL-004", "§4.2")}, None),
    ("BQ-026", "How long does the 3-month reduced payment programme last, and what share of the payment is reduced?",
     {("POL-COLL-004", "§4.2"), ("HPT-2026", "HP-LOAN-RED3")}, None),
    ("BQ-033", "What is the grace period before a promise to pay is considered broken?",
     {("PRC-COLL-010", "§3.3"), ("FAQ-COLL-001", "Q4")}, None),
    ("BQ-009", "A customer tells us they filed a consumer proposal. What should the agent do?",
     {("POL-COLL-001", "§7.3"), ("FAQ-COLL-001", "Q3")}, None),
    ("P-01", "What time of day am I allowed to phone someone about their overdue account?", {("POL-COLL-001", "§5.1")}, None),
    ("P-02", "The client says the money they sent never showed up on their balance. What now?", {("PRC-COLL-012", "§6.1")}, None),
    ("P-03", "Someone is unhappy with how we treated them on the phone. What do I do?", {("POL-COLL-007", "§8.1")}, None),
    ("P-04", "Do I need proof of earnings before putting someone on a short relief plan?", {("POL-COLL-004", "§4.3")}, None),
    ("P-05", "A woman just lost her husband and is very upset on the call. How should I handle it?",
     {("POL-COLL-004", "§4.5"), ("SCR-VUL-EN", "(whole document)")}, None),
    ("P-06", "We charged people late fees because our app was down. Do they have to ask for a refund?",
     {("PRC-COLL-012", "§6.2")}, None),
    ("P-07", "Why did the dialler place so few outbound calls late last month?", {("IR-2026-0923", "(whole document)")}, None),
    ("P-08", "A lawyer representing the customer wrote to us. Can we keep phoning the customer?", {("POL-COLL-001", "§5.4")}, None),
    ("P-09", "Peut-on appeler un client au Québec le dimanche?", {("REG-SUM-001-QC", "(whole document)")}, "QC"),
    ("P-10", "Can we use someone's marital status when deciding how to collect?", {("POL-COLL-004", "§4.6")}, None),
]

MODES = ["bm25", "hybrid", "hybrid_rerank"]


def rank_of(sections, gold) -> int | None:
    for i, s in enumerate(sections, 1):
        if (s.doc_id, s.section_id) in gold:
            return i
    return None


def retrieval_table(ix: PolicyIndex, as_of: date) -> tuple[list[str], dict]:
    k = retrievers.settings()["top_k"]
    rows, summary = [], {}
    per_mode = {}
    for mode in MODES:
        kw = retrievers.hooks(mode)
        ranks, secs = [], 0.0
        for qid, q, gold, prov in PROBES:
            t = time.time()
            res = ix.search(q, as_of, province=prov, top_k=10, with_hardship=False, **kw)
            secs += time.time() - t
            ranks.append(rank_of(res.sections, gold))
        per_mode[mode] = ranks
        hits = sum(1 for r in ranks if r and r <= k)
        mrr = sum(1 / r for r in ranks if r) / len(ranks)
        summary[mode] = (hits, mrr, secs / len(PROBES))
    total = len(ix.whole_corpus("x", as_of, with_hardship=False).sections)
    summary["whole_corpus"] = (len(PROBES), None, 0.0)
    rows.append("| question | " + " | ".join(MODES) + " | whole_corpus |")
    rows.append("|---|" + "---:|" * (len(MODES) + 1))
    for i, (qid, q, gold, _) in enumerate(PROBES):
        cells = [str(per_mode[m][i]) if per_mode[m][i] else "miss" for m in MODES]
        rows.append(f"| {qid} {q[:60]}{'...' if len(q) > 60 else ''} | " + " | ".join(cells) + f" | all {total} |")
    return rows, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", action="store_true", help="also answer every question in every mode (LLM calls)")
    a = ap.parse_args()
    ix, as_of = PolicyIndex.load(), config.AS_OF_DATE
    k = retrievers.settings()["top_k"]
    rows, summary = retrieval_table(ix, as_of)
    out = ["# Policy retrieval: mode comparison", "",
           f"{len(PROBES)} questions with a known correct section (6 benchmark questions, 10 paraphrases). Cell = rank of "
           f"the first correct section among the sections returned (top 10 shown; the model reads the top {k}). "
           "Built by `python scripts/compare_policy_modes.py`.", "",
           "| mode | hit@5 | MRR | search time per question |", "|---|---:|---:|---:|"]
    for m, (hits, mrr, secs) in summary.items():
        out.append(f"| {m} | {hits}/{len(PROBES)} | {'-' if mrr is None else f'{mrr:.2f}'} | "
                   f"{'-' if m == 'whole_corpus' else f'{secs * 1000:.0f} ms'} |")
    out += ["", *rows, ""]

    if a.answers:
        from trustissues.ask.rag.answer import run_policy
        from trustissues.common.llm import LLM

        llm = LLM()
        out += ["## Answers", "", "| question | mode | cited the correct section | not found | seconds | answer |",
                "|---|---|---|---|---:|---|"]
        for qid, q, gold, prov in PROBES:
            for mode in ("hybrid_rerank", "whole_corpus"):
                t = time.time()
                run = run_policy(q, ix, llm=llm, as_of=as_of, user_role="agent", province=prov, mode=mode)
                r = run.response
                cited = any((c.source_id, c.section) in gold for c in r.citations)
                ans = (r.answer or "").replace("\n", " ").replace("|", "/")[:160]
                out.append(f"| {qid} | {mode} | {'yes' if cited else 'no'} | {r.not_found} | {time.time() - t:.1f} | {ans} |")
                print(qid, mode, "cited" if cited else "NOT cited", r.not_found, ans[:90])

    path = config.REPO_ROOT / "reports" / "policy_retrieval_comparison.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out[:8 + len(summary)]))
    print(f"\nreport: {path}")


if __name__ == "__main__":
    main()
