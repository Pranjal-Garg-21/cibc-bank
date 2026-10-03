"""Hard rules for next best action. Pure logic, no AI, no model.

Input: one case's flags. Output: the allowed actions, and for every removed action the rule that
blocked it (the first one, in RULES order). `wait` is never blocked.

Flags come from collections_cases and the CRM (customers) by crm_customer_id for now
(`load_case_flags`); they switch to gold.customer_safeguards later. CRM safety flags are read only
here, never as model features.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from trustissues.nba.actions import CATALOGUE

CHANNELS = ("phone", "sms", "email", "push", "mail")  # as in customer_safeguards consent_* (+ push)
REAL_TIME = ("phone", "sms")  # channels bound by contact hours


@dataclass(frozen=True)
class CaseFlags:
    cease_contact: bool = False
    deceased: bool = False
    insolvency: bool = False  # insolvency hold, bankruptcy or consumer proposal
    open_dispute: bool = False
    represented: bool = False  # third-party / legal representative: do not contact the customer directly
    do_not_call: bool = False  # national DNCL or internal do-not-call
    experiment_holdout: bool = False  # in the bank's randomised no_contact_holdout cell: leave it to the bank
    consent: dict[str, bool] = field(default_factory=dict)  # channel -> consented; missing = no consent
    contacts_last_7d: int = 0
    contact_cap_7d: int = 3
    permitted_call_hours: tuple[time, time] | None = None  # regulatory hours, local time
    preferred_window: tuple[time, time] | None = None  # the customer's preferred contact window
    planned_local_time: time | None = None  # when the contact would happen, customer-local


@dataclass(frozen=True)
class RuleResult:
    allowed: list[str]
    blocked: dict[str, str]  # action -> rule id


def _outside(t: time | None, window: tuple[time, time] | None) -> bool:
    return t is not None and window is not None and not (window[0] <= t < window[1])


# (rule id, applies to this action?) in priority order. Hard stops first.
RULES = [
    ("cease_contact", lambda f, a: f.cease_contact),
    ("deceased", lambda f, a: f.deceased),
    ("insolvency", lambda f, a: f.insolvency),
    ("open_dispute", lambda f, a: f.open_dispute),
    # keep the bank's randomised test clean: NBA does not add contact to its holdout cases
    ("experiment_holdout", lambda f, a: f.experiment_holdout),
    ("represented_by_third_party", lambda f, a: f.represented),
    ("consent", lambda f, a: not f.consent.get(a.channel, False)),
    ("do_not_call", lambda f, a: f.do_not_call and a.channel == "phone"),
    ("contact_limit", lambda f, a: f.contacts_last_7d >= f.contact_cap_7d),
    ("outside_permitted_hours", lambda f, a: a.channel in REAL_TIME and _outside(f.planned_local_time,
                                                                                 f.permitted_call_hours)),
    ("outside_preferred_window", lambda f, a: a.channel in REAL_TIME and _outside(f.planned_local_time,
                                                                                  f.preferred_window)),
]
HARD_STOPS = ("cease_contact", "deceased", "insolvency", "open_dispute")


def allowed_actions(flags: CaseFlags) -> RuleResult:
    allowed, blocked = [], {}
    for name, action in CATALOGUE.items():
        rule = None if not action.contact else next((r for r, applies in RULES if applies(flags, action)), None)
        if rule:
            blocked[name] = rule
        else:
            allowed.append(name)
    return RuleResult(allowed, blocked)


def _hhmm(v: str | None) -> time | None:
    try:
        return time.fromisoformat(v) if v else None
    except ValueError:
        return None


def _window(start: str | None, end: str | None) -> tuple[time, time] | None:
    s, e = _hhmm(start), _hhmm(end)
    return (s, e) if s and e else None


def load_case_flags(con, case_ids: list[str] | None = None, planned_local_time: time | None = None) -> dict[str, CaseFlags]:
    """Case flags from collections_cases plus CRM safety flags. Missing CRM row = no consent on any channel."""
    from trustissues.features import sources

    sources.register(con)
    where = ""
    if case_ids is not None:
        con.execute("CREATE OR REPLACE TEMP TABLE _rule_cases AS SELECT unnest(?::VARCHAR[]) AS case_id", [case_ids])
        where = "WHERE c.case_id IN (SELECT case_id FROM _rule_cases)"
    rel = con.sql(f"""
        SELECT c.case_id,
               coalesce(c.cease_contact_flag, false) OR coalesce(f.cease_communication_flag, false) AS cease_contact,
               coalesce(c.deceased_hold_flag, false) OR coalesce(f.deceased_flag, false) AS deceased,
               coalesce(c.insolvency_hold_flag, false) OR coalesce(f.bankruptcy_flag, false)
                   OR coalesce(f.consumer_proposal_flag, false) AS insolvency,
               coalesce(c.dispute_flag, false) AND coalesce(c.dispute_status, 'open') = 'open' AS open_dispute,
               coalesce(c.third_party_rep_flag, false) OR coalesce(f.legal_representative_flag, false) AS represented,
               coalesce(f.national_dncl_flag, false) OR coalesce(f.internal_do_not_call_flag, false) AS do_not_call,
               coalesce(c.test_cell = 'no_contact_holdout', false) AS experiment_holdout,
               coalesce(f.consent_call, false), coalesce(f.consent_sms, false), coalesce(f.consent_email, false),
               coalesce(f.consent_push, false), coalesce(f.consent_letter, false),
               coalesce(c.contacts_last_7d, 0), coalesce(c.contact_cap_7d, 3),
               f.permitted_call_start_local, f.permitted_call_end_local,
               f.preferred_contact_window_start, f.preferred_contact_window_end
        FROM src_cases c LEFT JOIN src_crm_safety_flags f USING (crm_customer_id) {where}""")
    out = {}
    for (case_id, cease, dead, insolv, dispute, rep, dnc, held, c_call, c_sms, c_email, c_push, c_letter, n7, cap,
         pstart, pend, wstart, wend) in rel.fetchall():
        out[case_id] = CaseFlags(
            cease_contact=cease, deceased=dead, insolvency=insolv, open_dispute=dispute, represented=rep,
            do_not_call=dnc, experiment_holdout=held,
            consent={"phone": c_call, "sms": c_sms, "email": c_email, "push": c_push, "mail": c_letter},
            contacts_last_7d=n7, contact_cap_7d=cap,
            permitted_call_hours=_window(pstart, pend), preferred_window=_window(wstart, wend),
            planned_local_time=planned_local_time,
        )
    return out
