"""Role-based access control: one table says what each staff role may do. Everything else is refused.

Every route asks this module; the browser's menu is built from the same table (`can_map`) but is only a convenience.
Capabilities are named for the job, not the screen, so a new screen cannot widen anyone's access by accident.

Separation of duties built into the table:
  * agents decide only on their own recommendations and approve nothing;
  * hardship specialists approve only hardship and vulnerability cases, never high balances or the digital batch;
  * team leads approve everything but reach only the QA oversight they need (decision review, not the audit log);
  * QA analysts see aggregates and the audit log, never a customer's case, and can approve or change nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import ApiError

# Approval kinds (nba.queue.approval_reason), most sensitive first.
ALL_APPROVALS = ("vulnerability", "hardship", "high_balance", "digital_batch")
SUPPORT_APPROVALS = ("vulnerability", "hardship")
CALL_ACTIONS = ("agent_call", "support_call")


@dataclass(frozen=True)
class RoleSpec:
    ask_role: str                    # the Ask / masking role (ask.types.ROLES)
    caps: frozenset[str]
    approvals: tuple[str, ...] = ()  # kinds of approval this role may give
    customers: str = "none"          # which customers Next best action may show: own | all | support | none


_COMMON = {"overview", "ask_policy"}

ROLES: dict[str, RoleSpec] = {
    "agent": RoleSpec("agent", frozenset(_COMMON | {"my_queue", "next_action", "case_view", "decide", "ask_case"}),
                      customers="own"),
    "senior_agent": RoleSpec("agent", frozenset(_COMMON | {"my_queue", "next_action", "case_view", "decide", "ask_case"}),
                             customers="own"),
    # "decide": a recommendation the queue allocated to the lead themself (nba.review checks it is theirs).
    "team_lead": RoleSpec("team_lead", frozenset(_COMMON | {
        "next_action", "case_view", "decide", "approvals", "approve_batch", "manage_agents", "identity_review", "decision_review",
        "data_quality", "ask_case", "ask_portfolio", "tables"}), approvals=ALL_APPROVALS, customers="all"),
    # Ask role "agent": policy and cases assigned to them only. No portfolio numbers for the support desk.
    # The queue allocates recommendations to specialists too (1,440 on 28 Sep, 450 of them support calls), so they
    # work their own queue like an agent (as on the Streamlit pages) besides approving hardship and vulnerability.
    "hardship_specialist": RoleSpec("agent", frozenset(_COMMON | {
        "my_queue", "next_action", "case_view", "decide", "approvals", "ask_case"}), approvals=SUPPORT_APPROVALS,
        customers="support"),
    "qa_analyst": RoleSpec("analyst", frozenset(_COMMON | {
        "identity_review", "decision_review", "data_quality", "audit_log", "ask_portfolio"})),
}

# One sign-in form for everyone (D37): the role comes from curated.agents.role, never from the browser. Any role in
# this table may sign in; anything else fails like a wrong password.
SIGN_IN_ROLES = tuple(ROLES)
AGENT_ROLES = ("agent", "senior_agent")  # whose passwords a team lead sets (routers/team.py)
ASK_ROLE = {r: s.ask_role for r, s in ROLES.items()}
SUPERVISORS = ("team_lead", "hardship_specialist", "qa_analyst")  # who nba.agent_view lets see others' recommendations


def spec(role: str) -> RoleSpec | None:
    return ROLES.get(role)


def has(role: str, cap: str) -> bool:
    s = ROLES.get(role)
    return bool(s) and cap in s.caps


def require(user, cap: str) -> None:
    """Refuse (403) unless the signed-in user's role holds the capability. Unknown roles hold nothing."""
    if not has(user.role, cap):
        raise ApiError(403, "forbidden", "Your role does not have access to this.")


def approval_kinds(role: str) -> tuple[str, ...]:
    s = ROLES.get(role)
    return s.approvals if s else ()


def can_map(user, settings) -> dict[str, bool]:
    """What the browser may show. The server re-checks every request; this only shapes the menu."""
    h = lambda c: has(user.role, c)  # noqa: E731
    return {
        "overview": h("overview"),
        "my_queue": h("my_queue"),
        "next_action": h("next_action"),
        "case_view": h("case_view"),
        "decide": h("decide"),
        "approvals": h("approvals"),
        "approve_batch": h("approve_batch"),
        "manage_agents": h("manage_agents"),
        "identity_review": h("identity_review") and user.role in settings.identity_reviewer_roles,
        "decision_review": h("decision_review"),
        "status": h("data_quality"),
        "audit_log": h("audit_log"),
        "tables": h("tables") and settings.demo,
        "ask": True,
        "ask_case": h("ask_case"),
        "ask_portfolio": h("ask_portfolio"),
    }


def customer_scope(user) -> tuple[str, list]:
    """SQL fragment (starting ' AND ...') limiting gold.nba_decisions rows to those this role may open, with its
    parameters. Used by every customer-level read; `customers == 'none'` yields a clause that matches nothing."""
    s = ROLES.get(user.role)
    kind = s.customers if s else "none"
    if kind == "all":
        return "", []
    if kind == "own":
        return " AND agent_id = ?", [user.agent_id]
    if kind == "support":
        marks = ", ".join("?" for _ in SUPPORT_APPROVALS)
        return f" AND (approval_reason IN ({marks}) OR action = 'support_call')", list(SUPPORT_APPROVALS)
    return " AND 1 = 0", []
