"""Ask: one question through P2's ask() (the same function the benchmark runner uses), imported lazily.

The language model and policy search are off unless UI_ASK_LLM=1. LLM calls are audited into the UI's own audit
file. Result rows are masked for the asker's role with P2's governance.masking before they leave the server.
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..deps import AS_OF, current_user, warehouse
from ..errors import ApiError, not_connected
from ..security import clean_text, mask_rows
from ..serialize import jsonable

router = APIRouter(prefix="/api", tags=["ask"])

_LLM_LOCK = threading.Lock()


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


def _ask_module():
    try:
        from trustissues.ask import service
        from trustissues.ask.types import UserContext
    except ImportError as e:
        raise not_connected("Ask") from e
    return service, UserContext


def _llm_and_rag(app):
    """Built once per process when UI_ASK_LLM=1: the shared LLM client and P4's policy handler."""
    if not app.settings.ask_llm:
        return {}
    with _LLM_LOCK:
        if getattr(app, "ask_kwargs", None) is None:
            kw = {}
            try:
                from trustissues.common.llm import LLM
                kw["llm"] = LLM(audit=app.access_log.llm_audit())
                try:
                    from trustissues.ask.rag.policy_search import PolicyIndex
                    from trustissues.ask.rag_handler import make_rag_handler
                    kw["rag"] = make_rag_handler(PolicyIndex.load(), kw["llm"])
                except ImportError:
                    pass
            except ImportError:
                kw = {}
            app.ask_kwargs = kw
        return app.ask_kwargs


@router.post("/ask")
def ask(body: Question, request: Request):
    service, UserContext = _ask_module()
    app = request.app.state
    question = clean_text(body.question)
    with warehouse(app.settings) as con:
        user = current_user(request, con)
        wait = app.limiter.hit("ask", user.agent_id)
        if wait:
            raise ApiError(429, "rate_limited", f"Question limit reached. Try again in {wait} s.",
                           {"Retry-After": str(wait)})
        card = service.ask(question, UserContext(user.agent_id, user.ask_role), as_of_date=AS_OF, con=con,
                           **_llm_and_rag(app))
    app.access_log.record(action=f"ask:{card.path}", user_id=user.agent_id, role=user.role,
                          object_id=card.metric_id or "", request_id=request.state.request_id)
    columns, rows = mask_rows(list(card.columns), [list(r) for r in card.rows], user.ask_role)
    out = jsonable(card)
    out.update(columns=columns, rows=jsonable(rows[:500]), truncated=len(rows) > 500)
    if card.path == "sql" and card.columns:
        out["answer"] = ""  # for generated SQL ask() puts the CSV in `answer`; the masked table is shown instead
    return out


class Export(BaseModel):
    question: str = Field(max_length=500)
    rows: int = Field(ge=0, le=100_000)


@router.post("/ask/exported")
def exported(body: Export, request: Request):
    """The browser builds the CSV from rows it was already sent (masked); this records that it was saved."""
    app = request.app.state
    with warehouse(app.settings) as con:
        user = current_user(request, con)
    app.access_log.record(action="export_csv", user_id=user.agent_id, role=user.role,
                          object_id=f"{body.rows} rows", request_id=request.state.request_id)
    return {"ok": True}
