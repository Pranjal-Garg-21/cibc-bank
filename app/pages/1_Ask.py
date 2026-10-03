"""Ask (P2 router + P4 RAG): one question box for every role, answered from the published warehouse.

Every question goes through `ask/service.ask()`: the injection and refusal gates, then the router, which sends it to
a certified metric, a "why did it change" split, a generated query (labelled uncertified), the policy documents or one
customer's history. The role comes from the member of staff picked in the sidebar (team lead -> team_lead,
QA analyst -> analyst, everyone else -> agent) and decides which paths answer. Tables are masked by role
(governance/masking.py) before they are shown.

Metric queries read the app's warehouse connection inside a READ ONLY transaction (ask/data/execute.py). Model calls
go through common/llm.py and are logged to gold.audit_log; without an API key the rules still answer metric questions
and refusals, and anything that needs the model says so (cached model answers are still used).
"""
import os

import pandas as pd
import streamlit as st
from ui import AS_OF, acting_as, con

from trustissues.ask.access import case_customer_resolver
from trustissues.ask.rag_handler import make_rag_handler
from trustissues.ask.service import ask
from trustissues.ask.types import UserContext
from trustissues.common import config
from trustissues.common.audit import AuditLog
from trustissues.common.llm import LLM, _load_dotenv
from trustissues.governance.masking import mask_answer_card
from trustissues.nba import agent_view

ASK_ROLE = {"team_lead": "team_lead", "qa_analyst": "analyst"}  # everyone else asks as an agent
PATH_LABEL = {
    "metric": ("Certified metric", "green"), "drivers": ("What moved", "green"), "sql": ("Uncertified query", "orange"),
    "rag_corpus": ("Policy documents", "blue"), "rag_customer": ("Customer history", "violet"),
    "refused": ("Refused", "red"), "cannot_answer": ("Cannot answer", "gray"),
}
EXAMPLES = {
    "agent": ["What is the grace period before a promise to pay is considered broken?",
              "Is CASL express consent needed to send a collections payment reminder by email?"],
    "team_lead": ["What was the broken-promise rate by agent site for promises due in July-September 2026?",
                  "What share of outbound calls in September 2026 were outside permitted calling hours, by customer province?",
                  "What is the average days past due of open cases by queue?"],
    "analyst": ["What is the hardship rate of cases opened in 2026, by primary product?",
                "What was the right-party contact rate on outbound calls in September 2026, by local hour of day?",
                "What share of active card accounts are 1 or more days past due today, by card product?"],
}


@st.cache_resource
def policy_index():
    from trustissues.ask.rag.policy_search import PolicyIndex
    try:
        return PolicyIndex.load()
    except FileNotFoundError:
        return None


def model_configured() -> bool:
    _load_dotenv()
    return bool(os.environ.get((config.settings().get("llm") or {}).get("api_key_env", "")))


def show(card, role: str) -> None:
    card = mask_answer_card(card, role)
    label, color = PATH_LABEL.get(card.path, (card.path, "gray"))
    st.markdown(f":{color}-badge[{label}]" + (f" :gray-badge[{card.metric_id}]" if card.metric_id else ""))
    if card.refused:
        st.error(card.answer)
    elif card.path == "cannot_answer":
        st.warning(card.answer)
    elif card.rows:
        if card.answer.startswith("summary:"):
            st.info(card.answer.splitlines()[0].removeprefix("summary:").strip().capitalize())
        st.dataframe(pd.DataFrame(list(card.rows), columns=list(card.columns)), hide_index=True)
    else:
        st.write(card.answer)
    for note in card.notes:
        st.caption(note)
    if card.sources and card.path in ("rag_corpus", "rag_customer"):
        st.caption("Sources: " + ", ".join(card.sources))
    if card.sql:
        with st.expander("How this was computed"):
            st.code(card.sql, language="sql")
            if card.parameters:
                st.json({k: str(v) for k, v in card.parameters.items()})
            st.caption(("Certified template" if card.certified else "Generated query, checked by the validator") +
                       " · " + ", ".join(card.sources))


st.set_page_config(page_title="Ask", page_icon="💬", layout="wide")
c = con()
staff = acting_as(c)
if not staff:
    st.stop()
role = ASK_ROLE.get(staff["role"], "agent")
user = UserContext(staff["agent_id"], role)

st.title("Ask")
st.caption(f"As **{staff['agent_id']}** ({role.replace('_', ' ')}) · data as of {AS_OF}. Agents ask about policy and "
           "their own cases; team leads and analysts also ask about the portfolio's numbers.")
index = policy_index()
if not model_configured():
    st.sidebar.warning("No language model key: metric questions and refusals work; policy, customer and uncertified "
                       "answers need the model (cached answers still show).")
if index is None:
    st.sidebar.warning("Policy index not built: run `python -m trustissues.ask.rag.policy_ingest`.")

examples = list(EXAMPLES[role])
if role == "agent":
    # an agent may ask about any case in today's queue (assigned to them or allocated to them today)
    mine = agent_view.agent_queue(c, staff["agent_id"], AS_OF)
    if len(mine):
        examples.insert(0, f"Summarise the most recent recorded call on case {mine['case_id'].iloc[0]}: "
                           "what did the customer say and agree to?")
cols = st.columns(len(examples))
for col, q in zip(cols, examples):
    if col.button(q, use_container_width=True):
        st.session_state["ask_question"] = q
question = st.chat_input("Ask a question") or st.session_state.pop("ask_question", None)

history = st.session_state.setdefault("ask_history", [])
if question:
    with st.spinner("Answering..."):
        llm = LLM(audit=AuditLog(c))
        card = ask(question, user, as_of_date=AS_OF, con=c, llm=llm,
                   rag=make_rag_handler(index, llm, con=c), resolver=case_customer_resolver(c, AS_OF))
    history.insert(0, (staff["agent_id"], role, card))
for who, asked_as, card in history:
    if who != staff["agent_id"]:
        continue  # a different member of staff does not see what someone else asked
    with st.chat_message("user"):
        st.write(card.question)
    with st.chat_message("assistant"):
        show(card, asked_as)
