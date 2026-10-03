"""Agent queue (P3): today's recommendations for the member of staff acting.

Agents and hardship specialists see their own cases in working order: support calls first, then calls, then other
contact, then waits. Team leads see where the day stands, sign off the digital batch and work the approvals.
"""
import streamlit as st
from ui import ACTION_LABEL, AS_OF, STATE_LABEL, acting_as, con, open_card

from trustissues.nba import agent_view, review

st.set_page_config(page_title="Agent queue", page_icon="📋", layout="wide")
c = con()
user = acting_as(c)
if not user:
    st.warning("No staff found. Run the queue first: python -m trustissues.nba.queue --write-gold")
    st.stop()


def pick_and_open(df, key: str) -> None:
    """A table with one selectable row; selecting a row opens its decision card."""
    shown = df.assign(action=df["action"].map(ACTION_LABEL), state=df["state"].map(STATE_LABEL))
    cols = [c for c in ("case_id", "customer", "action", "risk_score", "expected_value", "approval_reason", "state",
                        "summary") if c in shown.columns]
    ev = st.dataframe(shown[cols], hide_index=True, use_container_width=True, on_select="rerun",
                      selection_mode="single-row", key=key,
                      column_config={"risk_score": st.column_config.ProgressColumn("Risk", min_value=0, max_value=1,
                                                                                    format="%.2f"),
                                     "expected_value": st.column_config.NumberColumn("Value (CAD)", format="$%.2f"),
                                     "case_id": "Case", "customer": "Customer", "action": "Action",
                                     "approval_reason": "Approval", "state": "State",
                                     "summary": st.column_config.TextColumn("Why", width="large")})
    rows = ev.selection.rows
    if rows:
        open_card(df.iloc[rows[0]]["recommendation_id"])


def agent_view_section() -> None:
    q = agent_view.agent_queue(c, user["agent_id"], AS_OF)
    st.subheader(f"Your queue for {AS_OF}")
    if q.empty:
        st.info("No recommendations assigned to you today.")
        return
    calls = q["action"].isin(["agent_call", "support_call"])
    m = st.columns(4)
    m[0].metric("Cases", f"{len(q):,}")
    m[1].metric("Calls to make", f"{int(calls.sum()):,}", help="Collections calls and support calls")
    m[2].metric("Support calls", f"{int((q['action'] == 'support_call').sum()):,}",
                help="Hardship or vulnerable customers: offer help first, no payment request before an option")
    m[3].metric("Need your decision", f"{int((q['state'] == 'waiting_agent').sum()):,}")
    st.caption("Select a row to open its decision card.")
    pick_and_open(q, "agent_queue")


def approvals_section(reasons: tuple[str, ...]) -> None:
    d = agent_view.lead_digest(c, AS_OF)
    pending = d["pending"][d["pending"]["approval_reason"].isin(reasons)]
    st.subheader(f"Approvals waiting ({len(pending):,})")
    if pending.empty:
        st.success("Nothing waiting for your approval.")
    else:
        st.caption("Select a row to open the case and approve or reject it.")
        pick_and_open(pending, "approvals")


if user["role"] == "team_lead":
    st.title("Team lead: today's queue")
    d = agent_view.lead_digest(c, AS_OF)
    m = st.columns(len(STATE_LABEL))
    for col, (k, label) in zip(m, STATE_LABEL.items()):
        col.metric(label, f"{d['states'].get(k, 0):,}")
    st.subheader("Digital reminder batch")
    st.write(f"{d['batch_total']:,} SMS, email and push reminders today; **{d['batch_pending']:,}** still need sign-off.")
    if d["batch_pending"] and st.button(f"Approve the digital batch ({d['batch_pending']:,})", type="primary"):
        try:
            res = review.approve_batch(c, user["agent_id"], AS_OF)
            st.success(f"Approved {res['approved']:,}, rejected {res['rejected']:,}.")
            st.rerun()
        except review.ReviewError as e:
            st.error(str(e))
    approvals_section(("vulnerability", "hardship", "high_balance"))
    with st.expander("Override review (last 7 days)"):
        from datetime import timedelta

        s = review.summary(c, AS_OF - timedelta(days=6), AS_OF)
        rate = "n/a" if s["override_rate"] is None else f"{s['override_rate']:.1%}"
        st.write(f"{s['agent_decisions']:,} agent decisions, override rate **{rate}**.")
        if s["safeguard_overrides"]:
            st.warning(f"{len(s['safeguard_overrides'])} overrides flagged hardship, vulnerability or a dispute.")
        st.json({k: s[k] for k in ("overrides_by_reason", "rejections_by_reason", "still_pending")})
else:
    st.title("Agent queue")
    agent_view_section()
    if user["role"] == "hardship_specialist":
        approvals_section(("vulnerability", "hardship"))
