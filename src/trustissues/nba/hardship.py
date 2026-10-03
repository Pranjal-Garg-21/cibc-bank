"""Hardship and vulnerability handling. Pure logic, no AI. Policy: POL-COLL-004 (§4.2, §4.4, §4.5).

A case is flagged when it has a hardship signal (collections_cases.hardship_flag; P4 text features later) or a
vulnerability flag (case or CRM). For a flagged case:

- No collections pressure. The only actions are `wait` and `support_call`: a certified agent calls to offer
  help, hardship programmes first, and asks for no payment before an option is offered (§4.2). Payment
  reminders (email, push, SMS, letter) and collections calls are never recommended. Every contact rule still
  applies to a support call (cease contact, insolvency, consent, contact limit, ...).
- Never harsher: the action is never harsher than what the case would get without the signal
  (support_call and wait both have harshness 0; MAX_HARSHNESS_IN_HARDSHIP = 0).
- A certified agent owns the case (agents.vulnerable_customer_certified, §4.5), whatever the action; the queue
  assigns one (nba/allocate.py `assign_owners`).
- A support call is due today (`support_reason`) when:
    offer_programme     hardship, and no programme has been offered yet (§4.2)
    plan_ending         hardship plan in place and ending within PLAN_REVIEW_DAYS (§4.4: contact 10 days before)
    follow_up           hardship, offered before but not on a plan, last offer at least FOLLOW_UP_DAYS ago
    vulnerable_contact  vulnerable only, and the case would otherwise be contacted today: a certified agent
                        makes that contact instead of a reminder or a collections call
  otherwise the case waits (a plan is running, or the customer is considering a recent offer).
- Approval is mandatory only when something is done (a support call), not for `wait`.
- Programme eligibility comes from hardship_programs valid on the as-of date (effective_from <= date <
  effective_to), checked against product_types, max_dpd, min_months_on_book, excludes_insolvency,
  excludes_broken_plan_90d, max_uses_12m and min_hardship_level. Eligible programmes go on the card.
- Hardship level (`level_from_case`): `clear` when the customer told us (recorded by an agent or a customer
  form), `possible` when only a model flagged it. Programmes needing `clear` are offered only then.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from trustissues.common.interfaces import HardshipOption
from trustissues.nba import actions, rules

LEVELS = ("none", "possible", "clear")  # matches hardship_programs.min_hardship_level
MAX_HARSHNESS_IN_HARDSHIP = 0  # wait or support_call only
SUPPORT = "support_call"
PLAN_REVIEW_DAYS = 10  # POL-COLL-004 §4.4: contact the customer 10 days before the plan ends
FOLLOW_UP_DAYS = 30  # do not offer again within 30 days of the last offer
STATED_SOURCES = ("agent_coded", "customer_form")  # the customer told us: hardship is `clear`


@dataclass(frozen=True)
class HardshipSignal:
    level: str = "none"  # none | possible | clear
    vulnerable: bool = False

    @property
    def active(self) -> bool:
        return self.level != "none" or self.vulnerable


@dataclass(frozen=True)
class HardshipHistory:
    """What has been done about the hardship so far, known before the as-of date."""
    plan_end: date | None = None  # end of an accepted hardship plan
    ever_offered: bool = False
    last_offer_ts: datetime | None = None


@dataclass(frozen=True)
class AccountContext:
    product: str  # card, personal_loan, auto_loan, loc, mortgage, overdraft
    dpd: int
    months_on_book: int
    insolvency: bool = False
    broken_plan_90d: bool = False
    uses_12m: dict[str, int] = field(default_factory=dict)  # program_id -> plans accepted in the last 12 months


@dataclass(frozen=True)
class Decision:
    action: str
    baseline_action: str  # what the case would get with no hardship signal
    route_to_certified_agent: bool
    mandatory_approval: bool
    rule_result: rules.RuleResult
    support_reason: str | None = None


def level_from_case(hardship_flag: bool, source: str | None) -> str:
    if not hardship_flag:
        return "none"
    return "clear" if source in STATED_SOURCES else "possible"


def support_reason(signal: HardshipSignal, history: HardshipHistory, baseline: str, as_of: date) -> str | None:
    """Why a certified agent should call today, or None (the case waits)."""
    if not signal.active:
        return None
    if signal.level != "none":
        if history.plan_end is not None and history.plan_end >= as_of:
            return "plan_ending" if (history.plan_end - as_of).days <= PLAN_REVIEW_DAYS else None
        if not history.ever_offered:
            return "offer_programme"
        if history.last_offer_ts is not None and (as_of - history.last_offer_ts.date()).days < FOLLOW_UP_DAYS:
            return None
        return "follow_up"
    return "vulnerable_contact" if actions.CATALOGUE[baseline].contact else None


def waiting_reason(signal: HardshipSignal, history: HardshipHistory, as_of: date) -> str | None:
    """Why a flagged case with no support call due waits: plan_running | recent_offer | no_contact_due."""
    if not signal.active:
        return None
    if signal.level != "none" and history.plan_end is not None and history.plan_end >= as_of:
        return "plan_running"
    if signal.level != "none" and history.last_offer_ts is not None:
        return "recent_offer"
    return "no_contact_due"


def decide(flags: rules.CaseFlags, values: dict[str, float], signal: HardshipSignal,
           history: HardshipHistory | None = None, as_of: date | None = None) -> Decision:
    """Rules first, then value, then the hardship adjustment (which only ever lowers harshness)."""
    result = rules.allowed_actions(flags)
    baseline = actions.choose(result.allowed, values)
    if not signal.active:
        return Decision(baseline, baseline, False, False, result)
    if as_of is None:
        from trustissues.common import config

        as_of = config.AS_OF_DATE
    reason = support_reason(signal, history or HardshipHistory(), baseline, as_of)
    action = SUPPORT if reason and SUPPORT in result.allowed else "wait"
    return Decision(action, baseline, True, action != "wait", result, reason)


def eligible_programs(programs: list[dict], as_of: date, ctx: AccountContext, level: str) -> list[dict]:
    rank = LEVELS.index(level)
    out = []
    for p in programs:
        reasons = [
            not (p["effective_from"] <= as_of < p["effective_to"]),
            ctx.product not in (p["product_types"] or []),
            p["max_dpd"] is not None and ctx.dpd > p["max_dpd"],
            p["min_months_on_book"] is not None and ctx.months_on_book < p["min_months_on_book"],
            bool(p["excludes_insolvency"]) and ctx.insolvency,
            bool(p["excludes_broken_plan_90d"]) and ctx.broken_plan_90d,
            p.get("max_uses_12m") is not None and ctx.uses_12m.get(p["program_id"], 0) >= p["max_uses_12m"],
            rank < LEVELS.index(p["min_hardship_level"] or "none"),
        ]
        if not any(reasons):
            out.append(p)
    return out


def options(programs: list[dict], as_of: date, ctx: AccountContext, level: str) -> list[HardshipOption]:
    """Eligible programmes as card entries. None for a case with no hardship signal (vulnerability alone)."""
    if level == "none":
        return []
    return [HardshipOption(p["program_id"], p["version"], p["program_name"], p.get("policy_section"),
                           p.get("approver_level"))
            for p in sorted(eligible_programs(programs, as_of, ctx, level), key=lambda p: p["program_id"])]


def load_programs(con) -> list[dict]:
    from trustissues.features import sources

    rel = sources.hardship_programs(con)
    return [dict(zip(rel.columns, r)) for r in rel.fetchall()]
