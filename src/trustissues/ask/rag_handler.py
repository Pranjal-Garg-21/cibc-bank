"""Connects Ask's `rag` hook to P4's policy path. Customer RAG stays honest-but-empty until P4 builds it."""
from __future__ import annotations

from trustissues.common.interfaces import RagRequest, RagResponse
from trustissues.common.llm import LLM, LLMError

from .rag import answer as policy_answer


def make_rag_handler(index, llm: LLM | None = None):
    """-> callable(RagRequest) -> RagResponse for `ask(..., rag=...)`.

    `index` is a `PolicyIndex` (load it once). A model outage returns `not_found` with the reason in `coverage`,
    never an invented answer.
    """
    llm = llm or LLM()

    def handle(req: RagRequest) -> RagResponse:
        if req.question_type == "rag_corpus":
            try:
                return policy_answer.answer_policy(req.question, index, llm=llm, as_of=req.as_of_date,
                                                   user_role=req.user_role, user_id=req.user_id)
            except LLMError as e:
                return RagResponse(answer=None, not_found=True, coverage=f"language model unavailable: {e}")
        return RagResponse(answer=None, not_found=True, coverage="customer notes and calls are not connected yet")

    return handle
