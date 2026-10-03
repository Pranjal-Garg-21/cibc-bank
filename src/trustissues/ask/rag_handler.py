"""Connects Ask's `rag` hook to P4's two RAG paths through interface I6 (RagRequest -> RagResponse).

    rag_corpus    ask/rag/answer.answer_policy          policy documents, citations checked
    rag_customer  ask/rag/customer_path.answer_customer  one customer's own history (gold.customer_timeline)

The router has already checked the role and the case (access.check_case); the customer path checks the customer
again against the assignment tables, because it is the one that reads customer text. Its refusal comes back as
`AccessDenied` with the router's wording, so `ask()` refuses a case the user may not see exactly as it refuses a case
that does not exist.
"""
from __future__ import annotations

from trustissues.common.interfaces import RagRequest, RagResponse
from trustissues.common.llm import LLM, LLMError

from .access import NOT_VISIBLE, AccessDenied
from .rag import answer as policy_answer
from .rag import customer_path


def make_rag_handler(index, llm: LLM | None = None, *, con=None):
    """-> callable(RagRequest) -> RagResponse for `ask(..., rag=...)`.

    `index` is a `PolicyIndex` (load it once); None (not built) makes policy questions say so. `con` is an open warehouse connection holding gold.customer_timeline
    and gold.case_customer; without it the customer path says plainly that it is not connected. A model outage
    returns `not_found` with the reason in `coverage`, never an invented answer.
    """
    llm = llm or LLM()

    def handle(req: RagRequest) -> RagResponse:
        try:
            if req.question_type == "rag_corpus":
                if index is None:
                    return RagResponse(answer=None, not_found=True, coverage="policy index not built: run "
                                       "`python -m trustissues.ask.rag.policy_ingest`")
                return policy_answer.answer_policy(req.question, index, llm=llm, as_of=req.as_of_date,
                                                   user_role=req.user_role, user_id=req.user_id,
                                                   **({"con": con} if con is not None else {}))
            if req.question_type == "rag_customer":
                if con is None:
                    return RagResponse(answer=None, not_found=True, coverage="customer history is not connected")
                try:
                    return customer_path.answer_customer(req, con=con, llm=llm)
                except customer_path.CustomerAccessDenied:
                    raise AccessDenied(NOT_VISIBLE) from None  # the router's wording: a missing case reads the same
        except LLMError as e:
            return RagResponse(answer=None, not_found=True, coverage=f"language model unavailable: {e}")
        return RagResponse(answer=None, not_found=True, coverage=f"unknown RAG path: {req.question_type}")

    return handle
