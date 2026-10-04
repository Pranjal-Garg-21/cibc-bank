"""Facts about one named case: status, queue, bucket, days past due, balance, last contact, promise, assigned agent
and today's next best action.

A fixed, reviewed lookup. No model writes SQL here and no model sees the rows. Access is decided before this runs
(`access.check_path` and `access.check_case`); this module never reads text from the question except to pick which
of the fixed facts to show. It returns no names, contact details or free text from notes.

Reads `gold.semantic_cases`, `gold.semantic_contacts`, `gold.semantic_promises`, `curated.collections_cases` (the
overdue amount, which the semantic views do not carry) and `gold.nba_decisions`. Like the case resolver, these reads
sit outside the SQL validator, and each is bounded by the as-of date.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb

# fact -> wording that asks for it. A question with a case ID and none of these gets the whole summary.
FACT_WORDS = {
    "status": r"\b(status|state|outcome|open|closed|resolved|cured|hold|stage)\b",
    "queue": r"\b(queue|team)\b",
    "bucket": r"\b(bucket|delinquen\w*|arrears)\b",
    "dpd": r"\b(days? past due|dpd|how (?:late|overdue))\b",
    "balance": r"\b(balance|overdue|owing|owed|amount|exposure|how much)\b",
    "last_contact": r"\b(last|latest|most recent|recent)\s+(?:contact|touch|attempt|reach)|\bwhen\b.*\bcontact(?:ed)?\b|\bcontact(?:ed)? (?:history|date)\b",
    "promise": r"\b(promise\w*|ptp|pledge\w*)\b",
    "agent": r"\b(agent|owner|owns|assigned|handling|handled by|who has)\b",
    "nba": r"\b(next best action|nba|next action|next step|what should (?:i|we|the agent) do|recommend\w*|"
           r"treatment|best (?:action|channel|time)|how (?:should|do) (?:i|we) (?:contact|reach)|channel|timing)\b",
}
# Actions and channels in staff words, as the web console labels them (ui/web/src/lib/labels.ts), never codes.
ACTION_WORDS = {"push_reminder": "app notification", "sms_reminder": "SMS reminder", "email_reminder": "email reminder",
                "agent_call": "call", "support_call": "support call", "letter": "letter", "wait": "wait"}
CHANNEL_WORDS = {"push": "mobile app notification", "sms": "SMS", "email": "email", "phone": "phone",
                 "mail": "letter by post"}
ALL_FACTS = ("status", "queue", "bucket", "dpd", "balance", "agent", "last_contact", "promise", "nba")


def wanted_facts(question: str) -> list[str]:
    asked = [f for f in ALL_FACTS if re.search(FACT_WORDS[f], question, re.I)]
    return asked or list(ALL_FACTS)


@dataclass
class CaseFacts:
    answer: str
    rows: list[tuple[str, str]]
    sql: str
    notes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


def _money(v) -> str:
    return "not recorded" if v is None else f"${float(v):,.2f}"


def _day(v) -> str:
    return "not recorded" if v is None else str(v)[:10]


def _status(con, case_id: str, as_of: date, log: list) -> tuple[dict, list[tuple[str, str]]]:
    sql = """SELECT case_id, primary_product, queue, canonical_bucket, current_dpd, outcome, outcome_date, cure_date,
                    case_open_date, hardship_flag, insolvency_hold_flag, deceased_hold_flag
             FROM gold.semantic_cases WHERE case_id = ?"""
    log.append((sql, [case_id]))
    r = con.execute(sql, [case_id]).fetchone()
    if r is None:
        return {}, []
    c = dict(zip(("case_id", "product", "queue", "bucket", "dpd", "outcome", "outcome_date", "cure_date", "opened",
                  "hardship", "insolvency_hold", "deceased_hold"), r))
    return c, []


def _overdue(con, case_id: str, log: list):
    sql = "SELECT total_overdue, total_exposure FROM curated.collections_cases WHERE case_id = ?"
    log.append((sql, [case_id]))
    try:
        return con.execute(sql, [case_id]).fetchone()
    except duckdb.Error:  # no curated layer on this connection (a fixture)
        log.pop()
        return None


def _last_contact(con, case_id: str, as_of: date, log: list):
    sql = """SELECT contact_ts_local, channel, direction, rpc_flag FROM gold.semantic_contacts
             WHERE case_id = ? AND contact_ts_local < CAST(? AS TIMESTAMP) ORDER BY contact_ts_local DESC LIMIT 1"""
    params = [case_id, as_of + timedelta(days=1)]
    log.append((sql, params))
    return con.execute(sql, params).fetchone()


def _promise(con, case_id: str, as_of: date, log: list):
    sql = """SELECT ptp_created_ts, ptp_due_date, ptp_status, amount_paid_against FROM gold.semantic_promises
             WHERE case_id = ? AND ptp_created_ts < CAST(? AS TIMESTAMP) ORDER BY ptp_created_ts DESC LIMIT 1"""
    params = [case_id, as_of + timedelta(days=1)]
    log.append((sql, params))
    return con.execute(sql, params).fetchone()


def _nba(con, case_id: str, as_of: date, user_id: str, role: str, log: list):
    """Today's recommendation for the case. An agent sees only one that is assigned to them; a team lead sees any.
    Returns (row or None, note or None)."""
    sql = """SELECT agent_id, action, channel, risk_score, requires_approval, approval_reason, decision_card,
                    recommendation_id
             FROM gold.nba_decisions WHERE case_id = ? AND as_of_date = ? ORDER BY expected_value DESC NULLS LAST LIMIT 1"""
    log.append((sql, [case_id, as_of]))
    try:
        r = con.execute(sql, [case_id, as_of]).fetchone()
    except duckdb.Error:
        log.pop()
        return None, "Next best action is not connected: there is no recommendation table in this warehouse."
    if r is None or (role == "agent" and r[0] != user_id):
        # the same words for "none generated" and "assigned to someone else", so an agent learns nothing about others' work
        where = "in your queue" if role == "agent" else "generated"
        return None, f"There is no recommendation for this case {where} for {as_of:%d %B %Y}."
    return r + (_review_status(con, r[7], as_of, log),), None


def _review_status(con, recommendation_id: str, as_of: date, log: list) -> dict | None:
    """What people have decided on it so far (nba/review.py: accepted / overridden, approved / rejected)."""
    from trustissues.nba import review
    log.append(("-- nba.review.status_of: latest decisions in gold.nba_overrides", [recommendation_id, as_of]))
    try:
        return review.status_of(con, recommendation_id, as_of)
    except duckdb.Error:  # no decisions table on this connection: nothing decided yet
        return None


def _approvers(reason) -> str:
    from trustissues.nba import review
    roles = review.APPROVERS.get(reason, ("team_lead",))
    return " or ".join(r.replace("_", " ") for r in roles)


def _state_text(st: dict | None, needs_approval: bool, reason) -> str | None:
    """The recommendation's state in words, from the review rules; None if nothing applies."""
    from trustissues.nba import review
    why = (st or {}).get("reason_code")
    why = f" ({review.REASON_CODES.get(why, str(why).replace('_', ' ')).lower()})" if why else ""
    state = (st or {}).get("state")
    if state == "overridden":
        return f"overridden by the assigned agent{why}: it will not be carried out today"
    if state == "blocked":
        return f"rejected at approval{why}: it will not be carried out today"
    if state == "waiting_approval" or (st is None and needs_approval):
        label, who = str(reason or "review").replace("_", " "), _approvers(reason)
        return f"needs a team lead's approval ({label})" if who == "team lead" else f"needs approval by a {who} ({label})"
    if state == "waiting_agent":
        return "waiting for the assigned agent to accept or override it"
    if state == "done":
        outcome = (st or {}).get("outcome")
        if (st or {}).get("completion") == "completed":
            return "done: the call has been made" + (
                f" ({review.CALL_OUTCOMES.get(outcome, str(outcome).replace('_', ' ')).lower()})" if outcome else "")
        return "done: the assigned agent accepted waiting today"
    if state == "ready":
        return "cleared to carry out (every sign-off it needs is in)"
    return None


def _nba_text(r) -> tuple[str, list[tuple[str, str]]]:
    _, action, channel, risk, needs_approval, reason, card_json, _rec_id, st = r
    try:
        card = json.loads(card_json or "{}")
    except ValueError:
        card = {}
    action_text = ACTION_WORDS.get(action, str(action).replace("_", " "))
    parts = [f"{action_text}" + (f" by {CHANNEL_WORDS.get(channel, channel)}" if channel and channel != "none" else "")]
    rows = [("next_best_action", action_text), ("recommended_channel", str(channel or "none"))]
    if card.get("contact_window"):
        parts.append(f"within {card['contact_window']}")
        rows.append(("contact_window", str(card["contact_window"])))
    if risk is not None:  # the case model's risk (nba/queue.py), not the promise model's
        rows.append(("risk_not_paid_up_30d", f"{float(risk):.2f}"))
    if needs_approval:
        rows.append(("needs_approval", str(reason or "yes")))
    state = _state_text(st, needs_approval, reason)
    if state:
        parts.append(state)
        rows.append(("status_today", state))
    text = ", ".join(parts)
    if card.get("summary"):
        text += f". {str(card['summary']).rstrip('.')}"
        rows.append(("why", str(card["summary"])))
    return text, rows


def lookup(con, case_id: str, question: str, *, as_of: date, assigned_agent_id: str | None, user_id: str, role: str) -> CaseFacts:
    facts = wanted_facts(question)
    log: list[tuple[str, list]] = []
    case, _ = _status(con, case_id, as_of, log)
    if not case:  # access passed on the resolver but the semantic view has no row: say so, never guess
        return CaseFacts(f"No record of case {case_id} was found in the case table.", [], "", ["case row missing"])

    rows: list[tuple[str, str]] = []
    sentences: list[str] = []
    notes: list[str] = []
    sources = ["gold.semantic_cases"]

    if "status" in facts:
        if case["outcome"] is None:
            holds = [n for n, k in (("insolvency", "insolvency_hold"), ("deceased estate", "deceased_hold")) if case[k]]
            status = "open" + (f", on {' and '.join(holds)} hold" if holds else "")
        else:
            status = f"closed: {case['outcome']}" + (f" on {_day(case['outcome_date'])}" if case["outcome_date"] else "")
        sentences.append(f"It is {status} (opened {_day(case['opened'])}, {case['product']})"
                         + ("; a hardship flag is set" if case["hardship"] else "") + ".")
        rows += [("status", status), ("opened", _day(case["opened"])), ("product", str(case["product"]))]
    if "queue" in facts:
        sentences.append(f"Queue: {case['queue'] or 'not recorded'}.")
        rows.append(("queue", str(case["queue"])))
    if "bucket" in facts:
        sentences.append(f"Delinquency bucket: {case['bucket'] or 'not recorded'}.")
        rows.append(("bucket", str(case["bucket"])))
    if "dpd" in facts:
        dpd = case["dpd"]
        sentences.append(f"Days past due: {'not recorded' if dpd is None else dpd}.")
        rows.append(("days_past_due", "" if dpd is None else str(dpd)))
    if "balance" in facts:
        bal = _overdue(con, case_id, log)
        if bal is None:
            notes.append("The overdue amount is not available on this connection.")
            sentences.append("The overdue amount is not available.")
        else:
            sources.append("curated.collections_cases")
            sentences.append(f"Overdue amount: {_money(bal[0])}; total exposure: {_money(bal[1])}.")
            rows += [("total_overdue", _money(bal[0])), ("total_exposure", _money(bal[1]))]
    if "agent" in facts:
        sentences.append(f"Assigned agent: {assigned_agent_id}." if assigned_agent_id else "No agent is assigned.")
        rows.append(("assigned_agent", assigned_agent_id or "unassigned"))
    if "last_contact" in facts:
        sources.append("gold.semantic_contacts")
        c = _last_contact(con, case_id, as_of, log)
        if c is None:
            sentences.append("There is no recorded contact on or before the as-of date.")
            rows.append(("last_contact", "none recorded"))
        else:
            text = f"{c[1]} ({c[2]}) on {_day(c[0])}, " + ("customer reached" if c[3] else "customer not reached")
            sentences.append(f"Last contact: {text}.")
            rows.append(("last_contact", text))
    if "promise" in facts:
        sources.append("gold.semantic_promises")
        p = _promise(con, case_id, as_of, log)
        if p is None:
            sentences.append("There is no promise to pay on this case.")
            rows.append(("latest_promise", "none"))
        else:
            paid = f", {_money(p[3])} paid against it" if p[3] else ""
            text = f"made {_day(p[0])}, due {_day(p[1])}, {p[2]}{paid}"
            sentences.append(f"Latest promise to pay: {text}.")
            rows.append(("latest_promise", text))
    if "nba" in facts:
        sources.append("gold.nba_decisions")
        rec, note = _nba(con, case_id, as_of, user_id, role, log)
        if rec is None:
            sentences.append(note)
            rows.append(("next_best_action", "none for you today" if role == "agent" else "none today"))
        else:
            text, nba_rows = _nba_text(rec)
            sentences.append(f"Next best action: {text}.")
            rows += nba_rows

    sql = "\n".join(f"-- query {i}\n{' '.join(q.split())}\n-- parameters: {[str(p) for p in params]}"
                    for i, (q, params) in enumerate(log, 1))
    answer = f"Case {case_id}. " + " ".join(sentences)
    return CaseFacts(answer, rows, sql, notes, sources)
