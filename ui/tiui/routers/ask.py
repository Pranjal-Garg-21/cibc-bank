"""Ask: one question through P2's ask() (the same function the benchmark runner uses), imported lazily.

The language model and policy search are off unless UI_ASK_LLM=1. LLM calls are audited into the UI's own audit
file. Result rows are masked for the asker's role with P2's governance.masking before they leave the server.

Wired the same way as the benchmark and the old Streamlit Ask page: P4's RAG handler reads the customer's history
through this request's warehouse connection (`make_rag_handler(index, llm, con=con)`), and case questions are
resolved with `case_customer_resolver(con, AS_OF)` (the case's assigned agent, or today's queue agent, may ask).
Without those two, a customer-call question answers "no case resolver is connected".
"""
from __future__ import annotations

import re
import threading

import duckdb
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import rbac
from ..deps import AS_OF, current_user, warehouse
from ..errors import ApiError, not_connected
from ..security import clean_text, mask_names, mask_rows, mask_text
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


def _llm_and_index(app) -> dict:
    """Built once per process when UI_ASK_LLM=1: the shared LLM client and P4's policy index (None if not built)."""
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
                    kw["index"] = PolicyIndex.load()
                except (ImportError, FileNotFoundError):
                    kw["index"] = None  # policy questions then say the index is not built
            except ImportError:
                kw = {}
            app.ask_kwargs = kw
        return app.ask_kwargs


def _ask_kwargs(app, con) -> dict:
    """What ask() needs besides the question: the case resolver always; the model and RAG handler with UI_ASK_LLM=1.
    The RAG handler is built per request because it reads customer history through this request's connection."""
    kw = {}
    try:
        from trustissues.ask.access import case_customer_resolver
        kw["resolver"] = case_customer_resolver(con, AS_OF)
    except ImportError:
        pass
    base = _llm_and_index(app)
    if "llm" in base:
        kw["llm"] = base["llm"]
        try:
            from trustissues.ask.rag_handler import make_rag_handler
            kw["rag"] = make_rag_handler(base.get("index"), base["llm"], con=con)
        except ImportError:
            pass
    return kw


def _model_configured() -> bool:
    try:
        import os

        from trustissues.common import config
        from trustissues.common.llm import _load_dotenv
        _load_dotenv()
        return bool(os.environ.get((config.settings().get("llm") or {}).get("api_key_env", "")))
    except ImportError:
        return False


EXAMPLES = {  # the Streamlit Ask page's examples, by Ask role (each is answered on the 4 Oct warehouse)
    "agent": ["What is the grace period before a promise to pay is considered broken?",
              "Is CASL express consent needed to send a collections payment reminder by email?"],
    "team_lead": ["What was the broken-promise rate by agent site for promises due in July-September 2026?",
                  "What share of outbound calls in September 2026 were outside permitted calling hours, by customer province?",
                  "What is the average days past due of open cases by queue?"],
    "analyst": ["What is the hardship rate of cases opened in 2026, by primary product?",
                "What was the right-party contact rate on outbound calls in September 2026, by local hour of day?",
                "What share of active card accounts are 1 or more days past due today, by card product?"],
}


def _case_with_call(con, cases: list[str]) -> str:
    """The first case (in queue order) with a recorded call before the as-of date, else the first case: the
    example then shows a real call summary instead of "no recorded calls on this case"."""
    try:
        hit = {r[0] for r in con.execute(
            f"""SELECT DISTINCT case_id FROM gold.customer_timeline WHERE event_type = 'transcript'
                AND event_ts < CAST(? AS TIMESTAMP) AND case_id IN ({", ".join("?" for _ in cases)})""",
            [AS_OF, *cases]).fetchall()}
    except duckdb.Error:  # no timeline on this warehouse: any case will do
        hit = set()
    return next((c for c in cases if c in hit), cases[0])


@router.get("/ask/examples")
def examples(request: Request):
    """Example questions for the asker's role, and whether the model and policy index are available.
    An agent also gets a question about the first case in today's queue (one they may ask about)."""
    app = request.app.state
    with warehouse(app.settings) as con:
        user = current_user(request, con)
        out = list(EXAMPLES.get(user.ask_role, EXAMPLES["agent"]))
        if user.ask_role == "agent" and rbac.has(user.role, "my_queue"):
            try:
                from trustissues.features import sources
                from trustissues.nba import agent_view
                sources.register(con)
                mine = list(agent_view.agent_queue(con, user.agent_id, AS_OF)["case_id"])
                if mine:
                    out.insert(0, f"Summarise the most recent recorded call on case {_case_with_call(con, mine)}: "
                                  "what did the customer say and agree to?")
            except ImportError:
                pass
    index = _llm_and_index(app).get("index") if app.settings.ask_llm else None
    return {"examples": out, "llm_enabled": bool(app.settings.ask_llm), "model_configured": _model_configured(),
            "policy_index": index is not None}


_CASE_ID = re.compile(r"\bCS-\d{4}-\d+\b", re.IGNORECASE)


def _customer_names(con, question: str) -> list[str]:
    """Full names of the people behind the cases named in the question (gold.case_customer -> gold.c360_customer).
    The model's case answer quotes notes and calls, which name the customer; scrub() cannot know names, so they are
    masked by name like the table's full_name column."""
    cases = sorted({c.upper() for c in _CASE_ID.findall(question)})
    if not cases:
        return []
    try:
        return [r[0] for r in con.execute(
            f"""SELECT DISTINCT p.full_name FROM gold.case_customer c JOIN gold.c360_customer p USING (golden_customer_id)
                WHERE c.case_id IN ({", ".join("?" for _ in cases)}) AND p.full_name IS NOT NULL""", cases).fetchall()]
    except duckdb.Error:
        return []


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
                           **_ask_kwargs(app, con))
        names = _customer_names(con, question) if card.path == "rag_customer" else []
    app.access_log.record(action=f"ask:{card.path}", user_id=user.agent_id, role=user.role,
                          object_id=card.metric_id or "", request_id=request.state.request_id)
    if card.path == "case_facts":
        # ask/case_facts.py returns fixed (field, value) pairs that are never names, contact details or note text. Column
        # masking would blank all of them (a "value" column is unclassified, so it fails closed); the values still go
        # through the text scrubber instead.
        columns = list(card.columns)
        rows = [[f, mask_text(str(v), user.ask_role) if v is not None else v] for f, v in card.rows]
    else:
        columns, rows = mask_rows(list(card.columns), [list(r) for r in card.rows], user.ask_role)
    out = jsonable(card)
    out.update(columns=columns, rows=jsonable(rows[:500]), truncated=len(rows) > 500)
    lines = (card.answer or "").splitlines()
    if card.columns and lines and (len(lines) > 1 or lines[0] == ",".join(card.columns)):
        # ask() writes the rows as CSV into `answer` (after a "summary:" line when it has one). The column-masked table
        # is shown instead, so only the summary is kept: the CSV copy would bypass the column masking.
        out["answer"] = lines[0].removeprefix("summary:").strip().capitalize() if lines[0].startswith("summary:") else ""
    # Free text (customer-history and generated answers, notes, the SQL and its bound values) can carry identifiers the
    # column masking never sees: the same scrubber as the Streamlit page (mask_answer_card -> text_controls.scrub)
    # replaces phones, emails, dates of birth, card and ID numbers. Case IDs, amounts and dates are left alone.
    out["answer"] = mask_names(mask_text(out["answer"], user.ask_role), names, user.ask_role)
    out["notes"] = [mask_names(mask_text(n, user.ask_role), names, user.ask_role) for n in out["notes"]]
    if out.get("sql"):
        out["sql"] = mask_text(out["sql"], user.ask_role)
    out["parameters"] = {k: mask_text(v, user.ask_role) if isinstance(v, str) else v
                         for k, v in (out.get("parameters") or {}).items()}
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
    wait = app.limiter.hit("write", user.agent_id)
    if wait:
        raise ApiError(429, "rate_limited", "Too many exports. Try again later.", {"Retry-After": str(wait)})
    app.access_log.record(action="export_csv", user_id=user.agent_id, role=user.role,
                          object_id=f"{body.rows} rows", request_id=request.state.request_id)
    return {"ok": True}
