"""Model fallback for the router: when no rule recognises a question, ask the model which catalogue entry it means.

Only questions that already passed the injection screen and the refusal gate get here, and the model sees the
question text and the catalogue's metric names and definitions, never data. Its choice is a path or one of the
catalogue's metric ids; anything else is treated as "cannot answer". A chosen metric still goes through
`build_call`, so an unsupported period or breakdown is still rejected there.
"""
from __future__ import annotations

import json

from trustissues.common.config import REPO_ROOT
from trustissues.common.llm import LLMError

from .catalog import TRIGGERS
from .router import Route

PROMPT_VERSION = "ask_route_v1"
PROFILES = REPO_ROOT / "config" / "metric_match_profiles.json"


def _metrics() -> dict[str, str]:
    allowed = {m for m, _ in TRIGGERS}
    data = json.loads(PROFILES.read_text(encoding="utf-8"))["metrics"]
    return {m["metric_id"]: f'{m["name"]}: {m["definition"]}' for m in data if m["metric_id"] in allowed}


def build_system() -> str:
    metrics = "\n".join(f"- {k}: {v}" for k, v in _metrics().items())
    return (
        "You route questions for a bank's collections analytics assistant. Choose exactly one path.\n"
        "Paths:\n"
        "- metric: asks for the value of one of the certified metrics below (a rate, share, average, count or amount, "
        "possibly by a breakdown or period). Give its metric_id.\n"
        "- rag_corpus: asks what a policy, regulation or procedure says or requires (rules, approvals, limits, "
        "what an agent should do or say).\n"
        "- sql: asks for a plain count or breakdown of current cases or accounts that no metric below covers.\n"
        "- cannot_answer: anything else, including unrelated topics or questions you are unsure about.\n"
        "A question about how many cases or accounts exist, by queue, product or bucket, is sql. Only choose metric when "
        "one definition clearly matches the quantity asked for; if two are plausible, choose cannot_answer.\n"
        f"Certified metrics:\n{metrics}\n"
        'Reply with JSON only: {"path": "...", "metric_id": "<id or null>"}'
    )


def llm_route(question: str, llm) -> Route | None:
    """The model's route for a question the rules could not place, or None if it has nothing usable to say."""
    try:
        res = llm.complete(system=build_system(), user=question, prompt_version=PROMPT_VERSION, tier="answer",
                           json_output=True, max_tokens=100)
    except (LLMError, KeyError):
        return None
    d = res.data if isinstance(res.data, dict) else {}
    path, metric = d.get("path"), d.get("metric_id")
    if path == "metric" and isinstance(metric, str) and metric in _metrics():
        return Route("metric", metric_id=metric, why="model fallback")
    if isinstance(path, str) and path in ("rag_corpus", "sql"):
        return Route(path, why="model fallback")
    return None
