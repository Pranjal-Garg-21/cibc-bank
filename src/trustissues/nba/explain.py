"""Decision-card explanations: template text, no AI.

- top reasons: the 3 features pushing risk up the most (SHAP), each as a plain sentence
- why the action beat the runner-up, from the action values (and capacity)
- a one-line summary, e.g. "High risk of not being paid up in 30 days (0.81): ...". nba/reword.py (stretch)
  may reword it later, with a fact check against these template facts.
"""
from __future__ import annotations

import math

from trustissues.common.interfaces import Reason
from trustissues.nba.actions import CATALOGUE

ACTION_TEXT = {
    "wait": "Wait: no contact today",
    "push_reminder": "Send an app push reminder",
    "email_reminder": "Send an email reminder",
    "sms_reminder": "Send an SMS reminder with a payment link",
    "letter": "Send a letter",
    "agent_call": "Call the customer",
    "support_call": "A certified agent calls to offer support (no payment request before an option is offered)",
}

# Hardship and vulnerable cases (nba/hardship.py): why a support call is due, or why the case waits.
SUPPORT_TEXT = {
    "offer_programme": "Hardship recorded and no programme offered yet: offer one before asking for payment "
                       "(POL-COLL-004 §4.2)",
    "plan_ending": "Hardship plan ends within 10 days: review it with the customer (POL-COLL-004 §4.4)",
    "follow_up": "Hardship programme offered over 30 days ago and not taken up: follow up (POL-COLL-004 §4.2)",
    "vulnerable_contact": "Vulnerable customer due for contact: a certified agent makes it instead of a reminder "
                          "or a collections call (POL-COLL-004 §4.5)",
}
WAIT_TEXT = {
    "plan_running": "Hardship plan in place: no collections contact while it runs",
    "recent_offer": "Hardship programme offered in the last 30 days: give the customer time to answer",
    "no_contact_due": "Vulnerable customer, no contact due today",
}


def support_text(action: str, support_reason: str | None, waiting_reason: str | None,
                 blocked_rule: str | None, no_capacity: list[str] | None) -> str:
    """One sentence for a hardship or vulnerable case: why the support call, or why it waits."""
    if action == "support_call":
        return SUPPORT_TEXT[support_reason]
    if support_reason and blocked_rule:
        return f"{SUPPORT_TEXT[support_reason]}, but contact is blocked ({blocked_rule.replace('_', ' ')})"
    if support_reason and no_capacity:
        return f"{SUPPORT_TEXT[support_reason]}, but no certified agent has a call free today"
    return WAIT_TEXT.get(waiting_reason, "No support call due today")


def _pct(v):
    return f"{v:.0%}"


def _n(v, unit):
    v = int(round(v))
    return f"{v} {unit}" + ("" if v == 1 else "s")


# feature -> sentence for its value. Features without a template fall back to "name = value".
TEMPLATES = {
    "ptp_to_overdue_ratio": lambda v: f"Promised {_pct(v)} of the overdue amount",
    "ptp_amount": lambda v: f"Promised ${v:,.0f}",
    "overdue_at_creation": lambda v: f"${v:,.0f} overdue when the promise was made",
    "dpd_at_creation": lambda v: f"{_n(v, 'day')} past due when the promise was made",
    "prior_broken_ptp_count": lambda v: f"{_n(v, 'earlier promise')} broken in this case",
    "ptp_sequence_in_case": lambda v: f"Promise number {int(v)} in this case",
    "days_to_due_at_creation": lambda v: f"Due {_n(v, 'day')} after the promise was made",
    "product": lambda v: f"Product: {v}",
    "ptp_channel": lambda v: f"Promise made by {v}",
    "ptp_type": lambda v: f"Promise type: {v}",
    "payment_method_promised": lambda v: f"Promised to pay by {v}",
    "reminder_scheduled_flag": lambda v: "Reminder scheduled" if v else "No reminder scheduled",
    "broken_ptp_90d": lambda v: f"{_n(v, 'promise')} broken in the last 90 days",
    "ptp_made_90d": lambda v: f"{_n(v, 'promise')} made in the last 90 days",
    "broken_ptp_365d": lambda v: f"{_n(v, 'promise')} broken in the last year",
    "kept_ptp_365d": lambda v: f"{_n(v, 'promise')} kept in the last year",
    "partial_ptp_365d": lambda v: f"{_n(v, 'promise')} partly kept in the last year",
    "days_since_last_broken": lambda v: f"Last broken promise {_n(v, 'day')} ago",
    "dpd_last_snapshot": lambda v: f"{_n(v, 'day')} past due at the last account snapshot",
    "worst_dpd_12m": lambda v: f"Worst arrears in the last year: {_n(v, 'day')} past due",
    "snapshots_delinquent_12m": lambda v: f"Behind at {_n(v, 'quarter-end')} in the last year",
    "card_utilisation": lambda v: f"Card utilisation {_pct(v)}",
    "balance_to_limit": lambda v: f"Balance at {_pct(v)} of the limit",
    "min_due_to_ptp_amount": lambda v: f"Minimum due is {v:.1f}x the promised amount",
    "payment_made_last_snapshot": lambda v: f"Paid ${v:,.0f} at the last snapshot",
    "paid_min_last_snapshot": lambda v: "Paid the minimum last time" if v else "Missed the minimum last time",
    "salary_snapshots_12m": lambda v: f"Salary seen at {_n(v, 'quarter-end')} in the last year",
    "salary_change_pct": lambda v: f"Salary changed {v:+.0%}",
    "ptp_amount_to_salary": lambda v: f"Promise is {_pct(v)} of monthly salary",
    "contacts_30d": lambda v: f"{_n(v, 'contact')} in the last 30 days",
    "calls_30d": lambda v: f"{_n(v, 'call')} in the last 30 days",
    "call_rpc_rate_30d": lambda v: f"Answered {_pct(v)} of calls in the last 30 days",
    "ptp_made_rate_30d": lambda v: f"Made a promise in {_pct(v)} of contacts in the last 30 days",
    "email_open_rate_30d": lambda v: f"Opened {_pct(v)} of emails in the last 30 days",
    "sms_click_rate_30d": lambda v: f"Clicked {_pct(v)} of SMS links in the last 30 days",
    "push_open_rate_30d": lambda v: f"Opened {_pct(v)} of push messages in the last 30 days",
    "days_since_last_contact": lambda v: f"Last contacted {_n(v, 'day')} ago",
    "grace_days": lambda v: f"{_n(v, 'grace day')} after the due date",
    "resolved_ptp_all": lambda v: f"{_n(v, 'earlier promise')} resolved",
    "days_since_snapshot": lambda v: f"Last account snapshot {_n(v, 'day')} old",
    "snapshot_account_level": lambda v: "Snapshot for the promised account" if v else "Snapshot across all accounts",
    "overlimit_last_snapshot": lambda v: "Over the limit at the last snapshot" if v else "Within the limit at the last snapshot",
    "salary_last": lambda v: f"Last salary credit ${v:,.0f}",
    "stated_income_date_flag": lambda v: "Gave an income date" if v else "Gave no income date",
    "payments_taken_30d": lambda v: f"{_n(v, 'payment')} taken on contacts in the last 30 days",
    # case model (case_state.sql)
    "case_age_days": lambda v: f"In collections for {_n(v, 'day')}",
    "primary_product": lambda v: f"Product: {v}",
    "case_open_reason": lambda v: f"Case opened for: {str(v).replace('_', ' ')}",
    "entry_dpd": lambda v: f"{_n(v, 'day')} past due when the case opened",
    "entry_overdue": lambda v: f"${v:,.0f} overdue when the case opened",
    "accounts_in_case_count": lambda v: f"{_n(v, 'account')} in the case",
    "prior_cases_12m": lambda v: f"{_n(v, 'earlier case')} in the year before",
    "dpd_now": lambda v: f"{_n(v, 'day')} past due now",
    "overdue_now": lambda v: f"${v:,.0f} overdue now",
    "dpd_change_since_open": lambda v: f"{_n(v, 'day')} further behind than when the case opened",
    "overdue_vs_entry": lambda v: f"Owes {v:.1f}x what was overdue when the case opened",
    "case_promises": lambda v: f"{_n(v, 'promise')} made in this case",
    "case_promises_broken": lambda v: f"{_n(v, 'promise')} broken in this case",
    "case_promises_kept": lambda v: f"{_n(v, 'promise')} kept in this case",
    "has_open_ptp": lambda v: "Has an open promise to pay" if v else "No open promise to pay",
    "open_ptp_to_overdue_ratio": lambda v: f"Open promise covers {_pct(v)} of the overdue amount",
    "days_to_open_ptp_due": lambda v: (f"Open promise due in {_n(v, 'day')}" if v >= 0
                                       else f"Open promise {_n(-v, 'day')} past its due date"),
    "case_contacts": lambda v: f"{_n(v, 'contact')} in this case",
    "case_rpc": lambda v: f"Reached {_n(v, 'time')} in this case",
    "case_payments_taken": lambda v: f"{_n(v, 'payment')} taken in this case",
    "days_since_case_contact": lambda v: f"Last contact in this case {_n(v, 'day')} ago",
}


# What a missing value means, for features where "no data" is itself the signal.
MISSING_TEXT = {
    "open_ptp_to_overdue_ratio": "No open promise to pay",
    "days_to_open_ptp_due": "No open promise to pay",
    "days_since_last_broken": "Never broke a promise before",
    "days_since_case_contact": "No contact in this case yet",
    "days_since_last_contact": "No contact in the last 30 days",
    "card_utilisation": "No credit card on file",
    "balance_to_limit": "No credit account snapshot",
    "salary_last": "No salary credits seen",
    "salary_change_pct": "No salary history",
    "call_rpc_rate_30d": "No calls in the last 30 days",
    "email_open_rate_30d": "No emails in the last 30 days",
    "sms_click_rate_30d": "No SMS in the last 30 days",
    "push_open_rate_30d": "No push messages in the last 30 days",
    "dpd_last_snapshot": "No account snapshot yet",
    "worst_dpd_12m": "No account snapshot yet",
}


def _missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def reason_text(feature: str, value) -> str:
    if _missing(value):
        return MISSING_TEXT.get(feature, f"No data for {feature.replace('_', ' ')}")
    fn = TEMPLATES.get(feature)
    return fn(value) if fn else f"{feature.replace('_', ' ')} = {value}"


def top_reasons(values: dict, contributions: dict[str, float], n: int = 3) -> list[Reason]:
    """The n features that push risk up the most (positive SHAP), as sentences. Two features saying the same
    thing (e.g. both missing because there is no open promise) count once."""
    out, seen = [], set()
    for f, c in sorted(((f, c) for f, c in contributions.items() if c > 0), key=lambda t: -t[1]):
        text = reason_text(f, values.get(f))
        if text not in seen:
            seen.add(text)
            out.append(Reason(feature=f, text=text, contribution=round(float(c), 4)))
        if len(out) == n:
            break
    return out


def risk_band(risk: float) -> str:
    return "High" if risk >= 0.6 else "Medium" if risk >= 0.3 else "Low"


def why_beat(action: str, runner_up: str | None, values: dict[str, float],
             no_capacity: list[str] | None = None, support: str | None = None,
             same_day: str | None = None) -> str | None:
    if same_day:  # one contact per person per day
        return f"The customer is contacted today about case {same_day}; one contact per person per day"
    if support:  # hardship / vulnerable case: policy, not value, decides
        return support
    if no_capacity:
        return f"{', '.join(no_capacity)} had no capacity left today; this was the next best"
    if runner_up is None:
        return None
    if action == "wait":
        return f"No contact is expected to recover more than it costs (best: {runner_up}, ${values[runner_up]:,.2f})"
    gap = values.get(action, 0.0) - values.get(runner_up, 0.0)
    return (f"Expected net ${values[action]:,.2f} vs ${values[runner_up]:,.2f} for {runner_up} "
            f"(${gap:,.2f} more after contact cost)")


def summary(action: str, risk: float, reasons: list[Reason], route_to_certified: bool,
            blocked_by_hard_stop: str | None, promise_risk: float | None = None,
            no_capacity: list[str] | None = None, support: str | None = None,
            options: list[str] | None = None, window: str | None = None, same_day: str | None = None,
            from_text: bool = False) -> str:
    head = f"{risk_band(risk)} risk of not being paid up in 30 days ({risk:.2f})"
    why = "; ".join(r.text for r in reasons[:2])
    parts = [f"{head}: {why}." if why else f"{head}."]
    if promise_risk is not None:
        parts.append(f"Open promise: {promise_risk:.2f} risk of breaking.")
    if blocked_by_hard_stop:
        parts.append(f"Contact blocked ({blocked_by_hard_stop.replace('_', ' ')}).")
    if no_capacity and not route_to_certified:
        parts.append(f"No capacity today for {', '.join(no_capacity)}.")
    if same_day:
        parts.append(f"Already contacted today about case {same_day}.")
    parts.append(ACTION_TEXT.get(action, action) + (f", between {window}." if window else "."))
    if route_to_certified:
        parts.append(f"Hardship or vulnerability: {support or 'handled by a certified agent'}.")
        if from_text:
            parts.append("Flagged from the customer's notes or calls (see the evidence), not from a recorded flag.")
        if options:
            parts.append(f"Eligible programmes: {', '.join(options)}.")
        parts.append("Owned by a certified agent.")
    return " ".join(parts)


def runner_up(action: str, values: dict[str, float]) -> str | None:
    others = sorted(((a, v) for a, v in values.items() if a != action), key=lambda t: (-t[1], CATALOGUE[t[0]].harshness))
    return others[0][0] if others else None
