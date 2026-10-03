"""Hardship and vulnerability handling. Pure logic, no AI.

- Any hardship or vulnerability sign routes the case to a certified agent
  (agents.vulnerable_customer_certified) and requires approval of the recommendation.
- Hardship can only make the treatment gentler: the recommended action is never harsher than what
  the case would get without the signal, and is capped at MAX_HARSHNESS_IN_HARDSHIP.
- Programme eligibility comes from hardship_programs valid on the as-of date
  (effective_from <= date < effective_to), checked against max_dpd, min_months_on_book,
  excludes_insolvency, excludes_broken_plan_90d, product_types and min_hardship_level.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from trustissues.nba import actions, rules

LEVELS = ("none", "possible", "clear")  # matches hardship_programs.min_hardship_level
MAX_HARSHNESS_IN_HARDSHIP = 1  # reminders only; outreach beyond that is the certified agent's call


@dataclass(frozen=True)
class HardshipSignal:
    level: str = "none"  # none | possible | clear (case hardship flag now; P4 text features later)
    vulnerable: bool = False

    @property
    def active(self) -> bool:
        return self.level != "none" or self.vulnerable


@dataclass(frozen=True)
class AccountContext:
    product: str  # card, personal_loan, auto_loan, loc, mortgage, overdraft
    dpd: int
    months_on_book: int
    insolvency: bool = False
    broken_plan_90d: bool = False


@dataclass(frozen=True)
class Decision:
    action: str
    baseline_action: str  # what the case would get with no hardship signal
    route_to_certified_agent: bool
    mandatory_approval: bool
    rule_result: rules.RuleResult


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
            rank < LEVELS.index(p["min_hardship_level"] or "none"),
        ]
        if not any(reasons):
            out.append(p)
    return out


def load_programs(con) -> list[dict]:
    from trustissues.features import sources

    rel = sources.hardship_programs(con)
    return [dict(zip(rel.columns, r)) for r in rel.fetchall()]


def decide(flags: rules.CaseFlags, values: dict[str, float], signal: HardshipSignal) -> Decision:
    """Rules first, then value, then the hardship adjustment (which can only lower harshness)."""
    result = rules.allowed_actions(flags)
    baseline = actions.choose(result.allowed, values)
    if not signal.active:
        return Decision(baseline, baseline, False, False, result)
    cap = min(MAX_HARSHNESS_IN_HARDSHIP, actions.harshness(baseline))
    gentle = [a for a in result.allowed if actions.harshness(a) <= cap]
    return Decision(actions.choose(gentle, values), baseline, True, True, result)
