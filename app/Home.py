"""TrustIssues app entry point.  streamlit run app/Home.py

Minimal placeholder so the P3 pages run: the plan gives Home.py (role picker, pipeline status) and the Ask page to P4,
who should replace this page. The demo user is picked in the sidebar on every page (app/ui.py).
"""
import streamlit as st
from ui import AS_OF, acting_as, con

st.set_page_config(page_title="TrustIssues", page_icon="🏦", layout="wide")
c = con()
user = acting_as(c)
st.title("TrustIssues: collections next best action")
st.write(f"Data as of **{AS_OF}**. Pick who you are in the sidebar, then open a page.")
if user:
    st.info(f"Acting as **{user['agent_id']}**, {user['role'].replace('_', ' ')} in team {user['team_id']}"
            + (" (certified for vulnerable customers)" if user["certified"] else "") + ".")
st.page_link("pages/2_Agent_Queue.py", label="Agent queue: today's recommendations", icon="📋")
st.page_link("pages/3_Decision_Card.py", label="Decision card: one case, why, and accept / override", icon="🗂️")
