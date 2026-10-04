"""Run an isolated, synthetic-data design preview. Never opens the project's warehouse or account file.

    .venv/bin/python ui/preview.py        # http://127.0.0.1:8000, demo-only passwords below
    .venv/bin/python ui/preview.py --mfa  # enrol the printed key in an authenticator to test MFA

Preview files are private and temporary; closing the process removes them. This is not a deployment entry point.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import duckdb
import pyotp
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tiui.accounts import AccountStore
from tiui.deps import AS_OF
from tiui.main import create_app
from tiui.settings import Settings

DEMO_PASSWORD = "Preview-only-2026!"


def build_preview(folder: Path, mfa: bool = False) -> Settings:
    wh = folder / "preview.duckdb"
    with duckdb.connect(str(wh)) as con:
        con.execute("CREATE SCHEMA curated; CREATE SCHEMA gold")
        con.execute("""CREATE TABLE curated.agents (agent_id VARCHAR, agent_display_name VARCHAR, team_id VARCHAR,
            role VARCHAR, vulnerable_customer_certified BOOLEAN, status VARCHAR, site VARCHAR, team_lead_id VARCHAR,
            languages VARCHAR, employment_type VARCHAR)""")
        con.execute("""INSERT INTO curated.agents VALUES
            ('DEMO-L1', 'Avery Laurent', 'Toronto collections', 'team_lead', true, 'active', 'Toronto', NULL, 'en|fr', 'full_time'),
            ('DEMO-A1', 'Maya Patel', 'Toronto collections', 'agent', true, 'active', 'Toronto', 'DEMO-L1', 'en', 'full_time'),
            ('DEMO-A2', 'Oliver Chen', 'Toronto collections', 'senior_agent', true, 'active', 'Toronto', 'DEMO-L1', 'en|fr', 'full_time'),
            ('DEMO-A3', 'Camille Roy', 'Toronto collections', 'agent', false, 'active', 'Toronto', 'DEMO-L1', 'fr', 'part_time')""")
        con.execute("""CREATE TABLE gold._publish_log (attempt_ts TIMESTAMP, published BOOLEAN, failed_rules VARCHAR,
            warned_rules VARCHAR, tables VARCHAR, secs DOUBLE)""")
        con.execute("INSERT INTO gold._publish_log VALUES (TIMESTAMP '2026-10-03 18:00:00', true, '', '', 'demo', 1.2)")
        con.execute("""CREATE TABLE gold._contract_results (rule_id VARCHAR, title VARCHAR, blocking BOOLEAN,
            violations BIGINT, total BIGINT, passed BOOLEAN, detail VARCHAR, checked_ts TIMESTAMP)""")
        con.execute("""INSERT INTO gold._contract_results VALUES
            ('Q1', 'Customer identifiers are unique', true, 0, 4, true, '', NULL),
            ('Q2', 'Required account fields are present', true, 0, 4, true, '', NULL),
            ('Q3', 'Dates fall within the reporting period', false, 1, 4, false, 'Synthetic example warning', NULL)""")
        con.execute("""CREATE TABLE gold.identity_review_queue (pair_id VARCHAR, node_a VARCHAR, node_b VARCHAR,
            reason VARCHAR, source_system_a VARCHAR, source_system_b VARCHAR, record_a VARCHAR, record_b VARCHAR,
            splink_probability DOUBLE, splink_explanation VARCHAR, llm_match BOOLEAN, llm_probability DOUBLE,
            llm_reason VARCHAR, llm_status VARCHAR, detail VARCHAR, evidence_key VARCHAR, status VARCHAR)""")
        _preview_next_actions(con)
    store = AccountStore(folder / "accounts.json")
    for sid in ("DEMO-L1", "DEMO-A1"):
        store.set_password(sid, DEMO_PASSWORD)
        if mfa:
            secret = pyotp.random_base32()
            store.enroll_mfa(sid, secret)
            print(f"{sid} authenticator setup key: {secret}")
    reports = folder / "reports"
    reports.mkdir()
    (reports / "data_quality_report.md").write_text(
        "# Demo data quality\n\nSynthetic preview data only. No customer records are loaded.\n", encoding="utf-8")
    return Settings(demo=True, require_mfa=mfa, idle_minutes=120, max_hours=2, warehouse=wh, accounts_file=store.path,
                    ui_audit=folder / "audit.duckdb", decisions_csv=folder / "decisions.csv", reports_dir=reports)


def _card(action, channel, risk, summary, window=None, runner=None, why=None, blocked=(), reasons=(), options=(),
          support=None, approval=None):
    return json.dumps({
        "recommended_action": action, "channel": channel, "risk_score": risk, "summary": summary,
        "contact_window": window, "runner_up_action": runner, "why_beat_runner_up": why,
        "blocked_actions": [{"action": a, "rule": r} for a, r in blocked],
        "top_reasons": [{"feature": f, "text": t, "contribution": c} for f, t, c in reasons],
        "hardship_options": list(options), "support_reason": support,
        "requires_approval": approval is not None, "approval_reason": approval, "evidence": [],
    })


def _preview_next_actions(con) -> None:
    """Synthetic gold.nba_decisions rows (same columns as nba/queue.py writes) for the Next best action screen."""
    con.execute("""CREATE TABLE gold.nba_decisions (recommendation_id VARCHAR, golden_customer_id VARCHAR,
        case_id VARCHAR, agent_id VARCHAR, as_of_date DATE, action VARCHAR, channel VARCHAR, risk_score DOUBLE,
        expected_value DOUBLE, requires_approval BOOLEAN, approval_reason VARCHAR, decision_card VARCHAR,
        model_version VARCHAR, feature_snapshot_id VARCHAR, created_ts TIMESTAMP)""")
    late = ("dpd_last_snapshot", "45 days past due at the last account snapshot", 0.21)
    broke = ("prior_broken_ptp_count", "2 earlier promises broken in this case", 0.14)
    opened = ("email_open_rate_30d", "Opens most reminder emails", -0.08)
    paid = ("paid_min_last_snapshot", "Paid the minimum last time", -0.11)
    rows = [
        ("G-0100001", "CS-2026-100001", "DEMO-A1", "agent_call", "phone", 0.71, 182.40, None, _card(
            "agent_call", "phone", 0.71, "High risk of non-payment and the customer answers calls: call today.",
            "09:00–18:00 customer-local", "sms_reminder", "A call is worth $182 against $64 for an SMS reminder.",
            [("letter", "contact_cap_7d")], [late, broke, paid])),
        ("G-0100002", "CS-2026-100002", "DEMO-A1", "sms_reminder", "sms", 0.38, 64.10, "digital_batch", _card(
            "sms_reminder", "sms", 0.38, "Moderate risk and a good SMS responder: send a reminder with a payment link.",
            "10:00–17:00 customer-local", "email_reminder", "SMS links are clicked twice as often as emails are opened.",
            [], [late, opened], approval="digital_batch")),
        ("G-0100003", "CS-2026-100003", "DEMO-A1", "support_call", "phone", 0.64, 0.0, "hardship", _card(
            "support_call", "phone", 0.64, "Hardship recorded and no programme offered yet: a certified agent calls to "
            "offer support before any payment request.", "09:00–17:00 customer-local", "agent_call",
            "Policy POL-COLL-004 §4.2 puts support ahead of collection.", [("sms_reminder", "hardship_first")],
            [late], [{"name": "Reduced payment plan", "months": 6}, {"name": "Interest pause", "months": 3}],
            support="offer_programme", approval="hardship")),
        ("G-0100004", "CS-2026-100004", "DEMO-A2", "email_reminder", "email", 0.22, 21.75, "digital_batch", _card(
            "email_reminder", "email", 0.22, "Low risk: a reminder email is enough today.", None, "wait",
            "An email costs $0.02 and lifts the chance of payment by 3 points.", [], [opened, paid],
            approval="digital_batch")),
        ("G-0100005", "CS-2026-100005", "DEMO-A1", "wait", None, 0.12, 0.0, None, _card(
            "wait", None, 0.12, "Promise to pay due in 3 days: no contact today.", None, "email_reminder",
            "Contacting before the promise is due does not raise the chance of payment.",
            [("agent_call", "open_promise")], [paid])),
        ("G-0100006", "CS-2026-100006", "DEMO-A2", "letter", "mail", 0.55, 12.30, None, _card(
            "letter", "mail", 0.55, "No consent for calls or texts: send a letter.", None, None, None,
            [("agent_call", "no_consent"), ("sms_reminder", "no_consent")], [late, broke])),
        ("G-0100001", "CS-2026-100011", "DEMO-A1", "wait", None, 0.33, 0.0, None, _card(
            "wait", None, 0.33, "Contacted on another case today: one contact per customer.", None, None, None,
            [("sms_reminder", "same_day_contact")], [late])),
    ]
    con.executemany("""INSERT INTO gold.nba_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preview', 'preview',
                       CAST(? AS TIMESTAMP))""",
                    [(f"REC-{AS_OF:%Y%m%d}-{case}", cust, case, agent, AS_OF, action, ch, risk, ev, reason is not None,
                      reason, card, f"{AS_OF} 06:00:00") for cust, case, agent, action, ch, risk, ev, reason, card in rows])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--mfa", action="store_true")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="cibc-ui-preview-") as name:
        settings = build_preview(Path(name), args.mfa)
        print(f"Preview: http://127.0.0.1:{args.port}", flush=True)
        print(f"Team lead: DEMO-L1 | Agent: DEMO-A1 | Demo password: {DEMO_PASSWORD}", flush=True)
        print("Synthetic data only. MFA is " + ("required." if args.mfa else "disabled for this labelled demo."), flush=True)
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, server_header=False,
                    access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
