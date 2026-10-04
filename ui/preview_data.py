"""Synthetic data for the design preview (ui/preview.py). Nothing here is real: names, IDs and amounts are invented.

Builds the same table shapes the real pipeline writes (curated.agents, gold.nba_decisions, gold.nba_overrides) plus a
few small `demo_src.*` tables that the stand-in P3 backend (preview_backend.py) reads for case facts. Deterministic:
the same seed gives the same screens on every run.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, time, timedelta

# (staff id, name, team, role, certified, site, lead, languages, employment, role label, password)
# Demo-only credentials for the synthetic preview. They open nothing but this temporary, fake warehouse.
STAFF = [
    ("DEMO-A1", "Maya Patel", "Toronto collections", "agent", True, "Toronto", "DEMO-L1", "en", "full_time",
     "Agent", "Agent-Maya-2026!"),
    ("DEMO-A2", "Oliver Chen", "Toronto collections", "senior_agent", True, "Toronto", "DEMO-L1", "en|fr", "full_time",
     "Agent", "Senior-Oliver-2026!"),
    ("DEMO-A3", "Camille Roy", "Toronto collections", "agent", False, "Toronto", "DEMO-L1", "fr", "part_time",
     "Agent", "Agent-Camille-2026!"),
    ("DEMO-L1", "Avery Laurent", "Toronto collections", "team_lead", True, "Toronto", None, "en|fr", "full_time",
     "Team lead", "Lead-Avery-2026!"),
    ("DEMO-H1", "Noor Haddad", "Hardship desk", "hardship_specialist", True, "Toronto", None, "en|fr", "full_time",
     "Hardship", "Hardship-Noor-2026!"),
    ("DEMO-Q1", "Priya Raman", "Quality assurance", "qa_analyst", False, "Toronto", None, "en", "full_time",
     "QA", "Quality-Priya-2026!"),
]

LATE = ("dpd_last_snapshot", "Well past due at the last account snapshot", 0.21)
BROKE = ("prior_broken_ptp_count", "Earlier promises broken in this case", 0.14)
OPENS = ("email_open_rate_30d", "Opens most reminder emails", -0.08)
PAID = ("paid_min_last_snapshot", "Paid the minimum last time", -0.11)
SMSR = ("sms_click_rate_30d", "Often taps payment links in texts", -0.1)
CALLR = ("call_rpc_rate_30d", "Usually answers calls", 0.09)
SALARY = ("salary_change_pct", "Salary credit dropped recently", 0.12)
PROGRAMMES = [{"name": "Reduced payment plan"}, {"name": "Interest pause"}, {"name": "Payment holiday"},
              {"name": "Term extension"}]


def card(action, channel, risk, summary, window=None, runner=None, why=None, blocked=(), reasons=(), options=(),
         support=None, approval=None, evidence=()):
    return json.dumps({
        "recommended_action": action, "channel": channel, "risk_score": risk, "summary": summary,
        "contact_window": window, "runner_up_action": runner, "why_beat_runner_up": why,
        "blocked_actions": [{"action": a, "rule": r} for a, r in blocked],
        "top_reasons": [{"feature": f, "text": t, "contribution": c} for f, t, c in reasons],
        "hardship_options": list(options), "support_reason": support, "requires_approval": approval is not None,
        "approval_reason": approval, "evidence": [dict(zip(("source_id", "source_ts", "quote"), e)) for e in evidence],
    })


def _hand_made():
    """The six cards written by hand: each shows a different corner of the recommendation."""
    return [
        ("G-0100001", "CS-2026-100001", "DEMO-A1", "agent_call", "phone", 0.71, 182.40, None, card(
            "agent_call", "phone", 0.71, "High risk of non-payment and the customer answers calls: call today.",
            "09:00–18:00 customer-local", "sms_reminder", "A call is worth $182 against $64 for an SMS reminder.",
            [("letter", "contact_cap_7d")], [LATE, BROKE, PAID, CALLR])),
        ("G-0100002", "CS-2026-100002", "DEMO-A1", "sms_reminder", "sms", 0.38, 64.10, "digital_batch", card(
            "sms_reminder", "sms", 0.38, "Moderate risk and a good SMS responder: send a reminder with a payment link.",
            "10:00–17:00 customer-local", "email_reminder", "SMS links are clicked twice as often as emails are opened.",
            [], [LATE, SMSR], approval="digital_batch")),
        ("G-0100003", "CS-2026-100003", "DEMO-A1", "support_call", "phone", 0.64, 0.0, "hardship", card(
            "support_call", "phone", 0.64, "Hardship recorded and no programme offered yet: a certified agent calls to "
            "offer support before any payment request.", "09:00–17:00 customer-local", "agent_call",
            "Policy POL-COLL-004 §4.2 puts support ahead of collection.", [("sms_reminder", "hardship_first")],
            [LATE, SALARY], PROGRAMMES[:2], support="offer_programme", approval="hardship",
            evidence=[("NOTE-88121", "2026-09-22", "I lost some hours at work this month, I can pay but not the full amount.")])),
        ("G-0100004", "CS-2026-100004", "DEMO-A2", "email_reminder", "email", 0.22, 21.75, "digital_batch", card(
            "email_reminder", "email", 0.22, "Low risk: a reminder email is enough today.", None, "wait",
            "An email costs $0.02 and lifts the chance of payment by 3 points.", [], [OPENS, PAID],
            approval="digital_batch")),
        ("G-0100005", "CS-2026-100005", "DEMO-A1", "wait", None, 0.12, 0.0, None, card(
            "wait", None, 0.12, "Promise to pay due in 3 days: no contact today.", None, "email_reminder",
            "Contacting before the promise is due does not raise the chance of payment.",
            [("agent_call", "open_promise")], [PAID])),
        ("G-0100006", "CS-2026-100006", "DEMO-A2", "letter", "mail", 0.55, 12.30, None, card(
            "letter", "mail", 0.55, "No consent for calls or texts: send a letter.", None, None, None,
            [("agent_call", "no_consent"), ("sms_reminder", "no_consent")], [LATE, BROKE])),
        ("G-0100001", "CS-2026-100011", "DEMO-A1", "wait", None, 0.33, 0.0, None, card(
            "wait", None, 0.33, "Contacted on another case today: one contact per customer.", None, None, None,
            [("sms_reminder", "same_day_contact")], [LATE])),
    ]


# kind -> (action, channel, approval, weight)
KINDS = {
    "call": ("agent_call", "phone", None, 17), "call_big": ("agent_call", "phone", "high_balance", 4),
    "sms": ("sms_reminder", "sms", "digital_batch", 9), "email": ("email_reminder", "email", "digital_batch", 6),
    "push": ("push_reminder", "push", "digital_batch", 3), "hardship": ("support_call", "phone", "hardship", 4),
    "vulnerable": ("support_call", "phone", "vulnerability", 3), "letter": ("letter", "mail", None, 3),
    "wait": ("wait", None, None, 6),
}
SUMMARY = {
    "call": "Risk of non-payment is high and the customer answers calls: call today.",
    "call_big": "Large overdue balance: a call is worth more than any reminder. Needs a team lead's sign-off.",
    "sms": "Moderate risk and a good SMS responder: send a reminder with a payment link.",
    "email": "Lower risk and the customer reads emails: a reminder email is enough today.",
    "push": "The customer is active in the banking app: send a push reminder.",
    "hardship": "Hardship recorded and no programme offered yet: a certified agent calls to offer support first.",
    "vulnerable": "Vulnerable customer due for contact: a certified agent calls instead of a reminder.",
    "letter": "No consent for calls or texts: send a letter.",
    "wait": "A promise to pay is due soon: no contact today.",
}
WINDOW = {"phone": "09:00–18:00 customer-local", "sms": "10:00–17:00 customer-local"}
SUPPORT = {"hardship": "offer_programme", "vulnerable": "vulnerable_contact"}
PRODUCTS = ["Credit card", "Line of credit", "Personal loan", "Auto loan", "Mortgage"]


def build(con, as_of: date) -> None:
    rnd = random.Random(2026)
    con.execute("CREATE SCHEMA IF NOT EXISTS demo_src")
    con.execute("""CREATE TABLE gold.nba_decisions (recommendation_id VARCHAR, golden_customer_id VARCHAR,
        case_id VARCHAR, agent_id VARCHAR, as_of_date DATE, action VARCHAR, channel VARCHAR, risk_score DOUBLE,
        expected_value DOUBLE, requires_approval BOOLEAN, approval_reason VARCHAR, decision_card VARCHAR,
        model_version VARCHAR, feature_snapshot_id VARCHAR, created_ts TIMESTAMP)""")
    con.execute("""CREATE TABLE gold.nba_overrides (recommendation_id VARCHAR, agent_id VARCHAR, decision VARCHAR,
        reason_code VARCHAR, ts TIMESTAMP)""")
    rows = _hand_made()
    kinds = [k for k, v in KINDS.items() for _ in range(v[3])]
    callers = ["DEMO-A1", "DEMO-A2", "DEMO-A1", "DEMO-A2", "DEMO-A3"]
    for i in range(44):
        k = rnd.choice(kinds)
        action, channel, approval, _ = KINDS[k]
        risk = round(rnd.uniform(0.5, 0.92) if k in ("call", "call_big", "hardship", "vulnerable")
                     else rnd.uniform(0.12, 0.58), 2)
        value = {"call": rnd.uniform(60, 240), "call_big": rnd.uniform(300, 900), "sms": rnd.uniform(25, 90),
                 "email": rnd.uniform(8, 40), "push": rnd.uniform(8, 35), "letter": rnd.uniform(6, 25)}.get(k, 0.0)
        pool = [a for a in callers if a != "DEMO-A3" or k in ("call", "call_big", "wait")]
        agent = None if approval == "digital_batch" else rnd.choice(pool)
        if k == "wait":
            agent = rnd.choice(callers)
        case = f"CS-2026-{100100 + i}"
        reasons = rnd.sample([LATE, BROKE, PAID, SALARY, CALLR, SMSR, OPENS], 3)
        opts = rnd.sample(PROGRAMMES, 2) if k in ("hardship", "vulnerable") else ()
        ev = [(f"NOTE-{90000 + i}", "2026-09-20", rnd.choice([
            "I can pay on the 30th when my pay clears.", "I was in the hospital last week, please be patient.",
            "My hours were cut, I need a lower payment."]))] if k in ("hardship", "vulnerable") else ()
        rows.append((f"G-{100100 + i:07d}", case, agent, action, channel, risk, round(value, 2), approval, card(
            action, channel, risk, SUMMARY[k], WINDOW.get(channel) if k != "wait" else None,
            "wait" if action != "wait" else "email_reminder",
            "The alternative costs more for a smaller chance of payment.", [], reasons, opts,
            support=SUPPORT.get(k), approval=approval, evidence=ev)))
    con.executemany("""INSERT INTO gold.nba_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preview-model-1',
                       'preview', CAST(? AS TIMESTAMP))""",
                    [(f"REC-{as_of:%Y%m%d}-{case}", cust, case, agent, as_of, action, ch, risk, ev, reason is not None,
                      reason, c, f"{as_of} 06:00:00") for cust, case, agent, action, ch, risk, ev, reason, c in rows])

    # Case facts for the decision card (what P3's case_context reads from the source tables).
    facts, contacts, promises = [], [], []
    for cust, case, *_ in rows:
        dpd = rnd.choice([8, 15, 24, 33, 47, 61, 78, 95])
        overdue = round(rnd.uniform(180, 2600), 2)
        facts.append((case, rnd.choice(PRODUCTS), (as_of - timedelta(days=dpd + rnd.randint(2, 20))).isoformat(), dpd,
                      overdue, "1-30" if dpd <= 30 else "31-60" if dpd <= 60 else "61-90" if dpd <= 90 else "90+",
                      rnd.choice(["A", "B", "holdout-C"]), False, False, False, rnd.randint(0, 3), 3))
        for j in range(rnd.randint(0, 3)):
            contacts.append((case, f"{as_of - timedelta(days=rnd.randint(1, 20))} 1{j}:15:00",
                             rnd.choice(["phone", "sms", "email"]), "outbound",
                             rnd.choice(["REMIND_1", "SOFT_CALL", "FIRM_CALL"]), rnd.random() < 0.5, rnd.random() < 0.2,
                             rnd.random() < 0.1))
        if rnd.random() < 0.4:
            promises.append((case, f"{as_of - timedelta(days=rnd.randint(4, 25))} 11:00:00", round(overdue * 0.5, 2),
                             0.5, (as_of - timedelta(days=rnd.randint(-5, 3))).isoformat(),
                             rnd.choice(["kept", "broken", "open"])))
    con.execute("""CREATE TABLE demo_src.case_facts (case_id VARCHAR, product VARCHAR, opened VARCHAR, days_past_due INT,
        total_overdue DOUBLE, bucket VARCHAR, test_cell VARCHAR, hardship BOOLEAN, vulnerable BOOLEAN, dispute BOOLEAN,
        contacts_last_7d INT, contact_cap_7d INT)""")
    con.executemany("INSERT INTO demo_src.case_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", facts)
    con.execute("""CREATE TABLE demo_src.contacts (case_id VARCHAR, "when" VARCHAR, channel VARCHAR, direction VARCHAR,
        treatment VARCHAR, reached BOOLEAN, promise_made BOOLEAN, payment_taken BOOLEAN)""")
    con.executemany("INSERT INTO demo_src.contacts VALUES (?,?,?,?,?,?,?,?)", contacts)
    con.execute("""CREATE TABLE demo_src.promises (case_id VARCHAR, made VARCHAR, amount DOUBLE, share_of_overdue DOUBLE,
        due VARCHAR, status VARCHAR)""")
    con.executemany("INSERT INTO demo_src.promises VALUES (?,?,?,?,?,?)", promises)

    _history(con, rnd, as_of, rows)


def _history(con, rnd, as_of: date, today) -> None:
    """Two weeks of past decisions (Camille overrides far more than anyone: an outlier for the review screen), then a
    few decisions already made today so the queue shows every state."""
    reasons = ["customer_already_paid", "customer_contacted_today", "customer_requested_channel", "too_much_contact",
               "contact_details_wrong", "card_facts_wrong", "other", "hardship_disclosed", "dispute_raised"]
    out = []
    rate = {"DEMO-A1": 0.12, "DEMO-A2": 0.09, "DEMO-A3": 0.46}
    for back in range(1, 15):
        day = as_of - timedelta(days=back)
        if day.weekday() == 6:
            continue
        for n in range(rnd.randint(14, 24)):
            agent = rnd.choices(["DEMO-A1", "DEMO-A2", "DEMO-A3"], [4, 4, 2])[0]
            rec = f"REC-{day:%Y%m%d}-CS-2026-{200000 + back * 100 + n}"
            ts = datetime.combine(day, time(8, 0)) + timedelta(minutes=rnd.randint(0, 540))
            if rnd.random() < rate[agent]:
                weights = [6, 5, 4, 3, 2, 2, 3, 2, 1] if agent != "DEMO-A3" else [2, 4, 2, 5, 1, 1, 6, 1, 1]
                out.append((rec, agent, "overridden", rnd.choices(reasons, weights)[0], ts))
            else:
                out.append((rec, agent, "accepted", None, ts))
        for n in range(rnd.randint(1, 3)):
            rec = f"REC-{day:%Y%m%d}-CS-2026-{300000 + back * 100 + n}"
            ts = datetime.combine(day, time(9, 0)) + timedelta(minutes=rnd.randint(0, 120))
            if rnd.random() < 0.2:
                out.append((rec, "DEMO-L1", "rejected", rnd.choice(["vulnerability_concern", "card_facts_wrong", "other"]), ts))
            else:
                out.append((rec, rnd.choice(["DEMO-L1", "DEMO-H1"]), "approved", None, ts))
    now = datetime.combine(as_of, time(8, 40))
    mine = [r for r in today if r[3] in ("agent_call",) and r[7] is None]
    for r in mine[:6]:  # a few calls already accepted, one already overridden
        out.append((f"REC-{as_of:%Y%m%d}-{r[1]}", r[2], "accepted", None, now))
    if len(mine) > 7:
        out.append((f"REC-{as_of:%Y%m%d}-{mine[7][1]}", mine[7][2], "overridden", "customer_already_paid", now))
    con.executemany("INSERT INTO gold.nba_overrides VALUES (?,?,?,?,?)", out)
