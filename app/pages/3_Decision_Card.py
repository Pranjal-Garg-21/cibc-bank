"""Decision card (P3): one recommendation, why, what was ruled out, the case's recent history, and the human decision.

The assigned agent accepts or overrides (a reason code is required to override); a team lead (or, for hardship and
vulnerability, a hardship specialist) approves or rejects when approval is required. nba/review.py checks every
decision. Evidence quotes come from P4's text features (nba/text_signals.py).
"""
import pandas as pd
import streamlit as st
from ui import ACTION_LABEL, AS_OF, STATE_COLOR, STATE_LABEL, acting_as, con

from trustissues.nba import agent_view, review

st.set_page_config(page_title="Decision card", page_icon="🗂️", layout="wide")
c = con()
user = acting_as(c)
if not user:
    st.stop()

# which recommendation: from the queue page, or pick one of yours
rec_id = st.session_state.get("recommendation_id")
if user["role"] not in agent_view.SUPERVISORS:
    mine = agent_view.agent_queue(c, user["agent_id"], AS_OF)
    if rec_id not in set(mine["recommendation_id"]):
        rec_id = mine["recommendation_id"].iloc[0] if len(mine) else None
    if len(mine):
        labels = dict(zip(mine["recommendation_id"], mine["case_id"] + " · " + mine["action"].map(ACTION_LABEL)))
        rec_id = st.sidebar.selectbox("Case", list(labels), index=list(labels).index(rec_id), format_func=labels.get)
else:
    rec_id = st.sidebar.text_input("Recommendation ID", value=rec_id or "")
if not rec_id:
    st.info("Open a case from the Agent queue page.")
    st.stop()
st.session_state["recommendation_id"] = rec_id

try:
    rec = agent_view.card(c, rec_id, user)
except KeyError:
    st.error(f"No recommendation {rec_id} for {AS_OF}.")
    st.stop()
except agent_view.AccessDenied as e:
    st.error(f"Not your case: {e}.")
    st.stop()

card, status = rec["card"], rec["status"]
action = card["recommended_action"]
st.title(f"{ACTION_LABEL.get(action, action)} · case {rec['case_id']}")
st.caption(f"Customer {rec['golden_customer_id']} · {rec['recommendation_id']} · model {rec['model_version']} · "
           f"feature snapshot {rec['feature_snapshot_id']}")

badges = [f":{STATE_COLOR.get(status['state'], 'gray')}-badge[{STATE_LABEL.get(status['state'], status['state'])}]"]
if card["requires_approval"]:
    badges.append(f":{'green' if status['approval'] == 'approved' else 'orange'}-badge[Approval: "
                  f"{card['approval_reason'].replace('_', ' ')}, {status['approval']}]")
else:
    badges.append(":gray-badge[No approval needed]")
if card.get("support_reason") or card.get("hardship_options"):
    badges.append(":violet-badge[Hardship / vulnerable]")
st.markdown(" ".join(badges))
m = st.columns(3)
m[0].metric("Risk: not paid up in 30 days", f"{card['risk_score']:.2f}")
m[1].metric("Expected value (CAD)", f"${rec['expected_value']:,.2f}",
            help="Extra chance of curing from the contact x amount overdue, minus its cost. Support calls are policy, "
                 "not valued.")
m[2].metric("Channel", (card["channel"] or "none").capitalize())
st.info(card["summary"])

left, right = st.columns(2)
with left:
    st.subheader("Why the risk is what it is")
    reasons = card["top_reasons"]
    if reasons:
        top = max(r["contribution"] for r in reasons) or 1
        for r in reasons:
            st.progress(min(1.0, r["contribution"] / top), text=f"{r['text']}  (+{r['contribution']:.2f})")
    st.subheader("Why this action")
    st.write(card["why_beat_runner_up"] or "Only action available.")
    if card["runner_up_action"]:
        st.caption(f"Runner-up: {ACTION_LABEL.get(card['runner_up_action'], card['runner_up_action'])}")
    if card.get("hardship_options"):
        st.subheader("Hardship programmes to offer first")
        st.dataframe(pd.DataFrame(card["hardship_options"]).rename(columns={
            "program_id": "Programme", "name": "Name", "version": "Version", "policy_section": "Policy",
            "approver_level": "Approved by"}), hide_index=True, use_container_width=True)
with right:
    st.subheader("Ruled out")
    blocked = pd.DataFrame(card["blocked_actions"])
    if blocked.empty:
        st.write("Nothing: every action was allowed.")
    else:
        blocked["action"] = blocked["action"].map(ACTION_LABEL).fillna(blocked["action"])
        blocked["rule"] = blocked["rule"].str.replace("_", " ")
        st.dataframe(blocked.rename(columns={"action": "Action", "rule": "Blocked by"}), hide_index=True,
                     use_container_width=True)
    st.subheader("Evidence from notes and calls")
    if card["evidence"]:
        for e in card["evidence"]:
            st.markdown(f"> {e['quote']}  \n— {e['source_id']}, {e['source_ts']}")
    else:
        st.caption("No quoted signal from this customer's notes or calls in the last 90 days (P4's text features, "
                   "gold.text_features).")

who = agent_view.customer_context(c, rec["golden_customer_id"], AS_OF)
with st.expander(f"The customer ({len(who['cases'])} case{'s' if len(who['cases']) != 1 else ''})",
                 expanded=len(who["cases"]) > 1):
    st.caption("One person across their CRM, card, loan and deposit records (identity resolution). Safeguards, the "
               "7-day contact limit and hardship history are theirs, across all these cases; they are contacted on "
               "one case a day at most.")
    a, b = st.columns(2)
    a.caption("Their cases")
    a.dataframe(who["cases"], hide_index=True, use_container_width=True)
    b.caption("Hardship offers on any of their cases")
    b.dataframe(who["offers"], hide_index=True, use_container_width=True)

ctx = agent_view.case_context(c, rec["case_id"], AS_OF)
with st.expander("The case", expanded=True):
    f = ctx["facts"]
    if f:
        st.write(f"{f['product']} · opened {f['opened']} · {f['days_past_due']} days past due "
                 f"(bucket {f['bucket']}) · ${f['total_overdue']:,.2f} overdue · contacts in the last 7 days "
                 f"{f['contacts_last_7d']} of {f['contact_cap_7d']} · test cell {f['test_cell']}"
                 + "".join(f" · **{k}**" for k in ("hardship", "vulnerable", "dispute") if f[k]))
    a, b = st.columns(2)
    a.caption("Last contacts")
    a.dataframe(ctx["contacts"], hide_index=True, use_container_width=True)
    b.caption("Last promises")
    b.dataframe(ctx["promises"], hide_index=True, use_container_width=True)

st.divider()
st.subheader("Your decision")
is_agent = user["agent_id"] == rec["agent_id"]
approver = card["requires_approval"] and user["role"] in review.APPROVERS.get(card["approval_reason"], ())
codes = list(review.REASON_CODES)


def act(decision: str, reason: str | None = None) -> None:
    try:
        review.record(c, rec_id, user["agent_id"], decision, reason)
        st.toast(f"Recorded: {decision}")
        st.rerun()
    except review.ReviewError as e:
        st.error(str(e))


if not (is_agent or approver):
    st.caption("Only the assigned agent can accept or override, and only an approver can approve this.")
if is_agent:
    a, b = st.columns([1, 3])
    if a.button("Accept", type="primary", use_container_width=True):
        act("accepted")
    with b.form("override"):
        reason = st.selectbox("Override reason", codes, format_func=lambda k: f"{k}: {review.REASON_CODES[k]}")
        if st.form_submit_button("Override"):
            act("overridden", reason)
if approver:
    a, b = st.columns([1, 3])
    if a.button("Approve", type="primary", use_container_width=True):
        act("approved")
    with b.form("reject"):
        reason = st.selectbox("Rejection reason", codes, format_func=lambda k: f"{k}: {review.REASON_CODES[k]}")
        if st.form_submit_button("Reject"):
            act("rejected", reason)
if len(rec["history"]):
    st.caption("Decisions so far")
    st.dataframe(rec["history"], hide_index=True, use_container_width=True)
