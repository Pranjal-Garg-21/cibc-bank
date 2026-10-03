"""Shared helpers for the Streamlit pages: the warehouse connection, who is acting, and labels.

The app holds the warehouse's write lock while it runs (decisions are written to gold.nba_overrides), so stop it
before running the Layer 1 pipeline or `python -m trustissues.nba.queue --write-gold`.
"""
from __future__ import annotations

import duckdb
import streamlit as st

from trustissues.common import config
from trustissues.common.interfaces import apply_stubs
from trustissues.features import sources
from trustissues.nba import agent_view

AS_OF = config.AS_OF_DATE
ACTION_LABEL = {"support_call": "Support call", "agent_call": "Call", "sms_reminder": "SMS reminder",
                "email_reminder": "Email reminder", "push_reminder": "Push reminder", "letter": "Letter", "wait": "Wait"}
STATE_LABEL = {"ready": "Ready", "waiting_agent": "Agent to decide", "waiting_approval": "To approve",
               "blocked": "Rejected", "overridden": "Overridden"}
STATE_COLOR = {"ready": "green", "waiting_agent": "orange", "waiting_approval": "orange", "blocked": "red",
               "overridden": "gray"}


@st.cache_resource
def _db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(config.warehouse_path()))
    apply_stubs(con)
    return con


def con() -> duckdb.DuckDBPyConnection:
    """A cursor for this run of the page, with the P3 source views registered."""
    c = _db().cursor()
    sources.register(c)
    return c


def acting_as(c) -> dict | None:
    """Sidebar picker for the member of staff the demo acts as. Remembered across pages."""
    people = agent_view.staff_list(c, AS_OF)
    labels = {r.agent_id: f"{r.agent_id} · {r.role.replace('_', ' ')} · team {r.team_id} · {r.calls} calls, {r.cases} cases"
              for r in people.itertuples()}
    ids = list(labels)
    if st.session_state.get("user_id") not in labels:
        st.session_state["user_id"] = ids[0] if ids else None
    # Streamlit drops a widget's value when the page changes, so the choice lives in "user_id" (not a widget key)
    # and is copied into the picker on every page; without this, opening a card switched back to the first person.
    st.session_state["_user_id_picker"] = st.session_state["user_id"]
    st.sidebar.selectbox("Acting as", ids, format_func=labels.get, key="_user_id_picker",
                         on_change=lambda: st.session_state.update(user_id=st.session_state["_user_id_picker"]))
    st.sidebar.caption(f"Data as of {AS_OF}. Demo login: pick any member of staff.")
    return agent_view.staff(c, st.session_state["user_id"]) if ids else None


def open_card(recommendation_id: str) -> None:
    st.session_state["recommendation_id"] = recommendation_id
    st.switch_page("pages/3_Decision_Card.py")
