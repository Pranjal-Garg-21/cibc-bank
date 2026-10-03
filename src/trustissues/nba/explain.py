"""Decision-card explanations: template text, no AI.

- top reasons: the 3 features pushing break risk up the most (SHAP), each as a plain sentence
- why the action beat the runner-up, from the action values
- a one-line summary, e.g. "High risk of breaking (0.81): ...". nba/reword.py (stretch) may reword it later,
  with a fact check against these template facts.
"""
from __future__ import annotations

import math

from trustissues.common.interfaces import Reason
from trustissues.nba.actions import CATALOGUE

ACTION_TEXT = {
    "wait": "Wait: no contact today",
    "push_reminder": "Send an app push reminder",
    "email_reminder": "Send an email reminder",
    "sms_payment_link": "Send an SMS payment link",
    "letter": "Send a letter",
    "outbound_call": "Call the customer",
}


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
    "snapshots_observed_12m": lambda v: f"{_n(v, 'account snapshot')} in the last year",
    "days_since_snapshot": lambda v: f"Last account snapshot {_n(v, 'day')} old",
    "snapshot_account_level": lambda v: "Snapshot for the promised account" if v else "Snapshot across all accounts",
    "overlimit_last_snapshot": lambda v: "Over the limit at the last snapshot" if v else "Within the limit at the last snapshot",
    "salary_last": lambda v: f"Last salary credit ${v:,.0f}",
    "stated_income_date_flag": lambda v: "Gave an income date" if v else "Gave no income date",
    "payments_taken_30d": lambda v: f"{_n(v, 'payment')} taken on contacts in the last 30 days",
}


def _missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def reason_text(feature: str, value) -> str:
    if _missing(value):
        return f"No data for {feature.replace('_', ' ')}"
    fn = TEMPLATES.get(feature)
    return fn(value) if fn else f"{feature.replace('_', ' ')} = {value}"


def top_reasons(values: dict, contributions: dict[str, float], n: int = 3) -> list[Reason]:
    """The n features that push break risk up the most (positive SHAP), as sentences."""
    up = sorted(((f, c) for f, c in contributions.items() if c > 0), key=lambda t: -t[1])[:n]
    return [Reason(feature=f, text=reason_text(f, values.get(f)), contribution=round(float(c), 4)) for f, c in up]


def risk_band(risk: float | None) -> str:
    if risk is None:
        return "No open promise"
    return "High" if risk >= 0.6 else "Medium" if risk >= 0.3 else "Low"


def why_beat(action: str, runner_up: str | None, values: dict[str, float]) -> str | None:
    if runner_up is None:
        return None
    gap = values.get(action, 0.0) - values.get(runner_up, 0.0)
    if action == "wait":
        return f"No contact action is expected to recover more than it costs (best: {runner_up}, ${values[runner_up]:,.2f})"
    return (f"Expected net ${values[action]:,.2f} vs ${values[runner_up]:,.2f} for {runner_up} "
            f"(${gap:,.2f} more after contact cost)")


def summary(action: str, risk: float | None, reasons: list[Reason], route_to_certified: bool,
            blocked_by_hard_stop: str | None) -> str:
    head = (f"{risk_band(risk)} risk of breaking ({risk:.2f})" if risk is not None else "No open promise")
    why = "; ".join(r.text for r in reasons[:2])
    parts = [f"{head}: {why}." if why else f"{head}."]
    if blocked_by_hard_stop:
        parts.append(f"Contact blocked ({blocked_by_hard_stop.replace('_', ' ')}).")
    parts.append(ACTION_TEXT.get(action, action) + ".")
    if route_to_certified:
        parts.append("Hardship or vulnerability sign: route to a certified agent.")
    return " ".join(parts)


def runner_up(action: str, values: dict[str, float]) -> str | None:
    others = sorted(((a, v) for a, v in values.items() if a != action), key=lambda t: (-t[1], CATALOGUE[t[0]].harshness))
    return others[0][0] if others else None
