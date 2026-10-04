"""Run an isolated, synthetic-data design preview. Never opens the project's warehouse or account file.

    .venv/bin/python ui/preview.py        # http://127.0.0.1:8000, one demo account per role (printed at start)
    .venv/bin/python ui/preview.py --mfa  # enrol the printed key in an authenticator to test MFA

Preview files are private and temporary; closing the process removes them. This is not a deployment entry point.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import duckdb
import pyotp
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent))

import preview_backend  # noqa: E402
import preview_data  # noqa: E402
from tiui.accounts import AccountStore
from tiui.deps import AS_OF
from tiui.main import create_app
from tiui.settings import Settings


def build_preview(folder: Path, mfa: bool = False) -> Settings:
    wh = folder / "preview.duckdb"
    with duckdb.connect(str(wh)) as con:
        con.execute("CREATE SCHEMA curated; CREATE SCHEMA gold")
        con.execute("""CREATE TABLE curated.agents (agent_id VARCHAR, agent_display_name VARCHAR, team_id VARCHAR,
            role VARCHAR, vulnerable_customer_certified BOOLEAN, status VARCHAR, site VARCHAR, team_lead_id VARCHAR,
            languages VARCHAR, employment_type VARCHAR)""")
        con.executemany("INSERT INTO curated.agents VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                        [s[:9] for s in preview_data.STAFF])
        con.execute("""CREATE TABLE gold._publish_log (attempt_ts TIMESTAMP, published BOOLEAN, failed_rules VARCHAR,
            warned_rules VARCHAR, tables VARCHAR, secs DOUBLE)""")
        con.execute("INSERT INTO gold._publish_log VALUES (TIMESTAMP '2026-09-28 05:40:00', true, '', '', 'demo', 1.2)")
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
        preview_data.build(con, AS_OF)
    store = AccountStore(folder / "accounts.json")
    for sid, *_rest, password in preview_data.STAFF:
        store.set_password(sid, password)
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


def print_accounts() -> None:
    print("Demo accounts (synthetic data only). Pick the matching tab on the sign-in page:", flush=True)
    for sid, name, _t, role, *_rest, tab, password in preview_data.STAFF:
        print(f"  {tab:<9} {sid:<8} {password:<22} {name} ({role.replace('_', ' ')})", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--mfa", action="store_true")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="cibc-ui-preview-") as name:
        settings = build_preview(Path(name), args.mfa)
        preview_backend.install()
        print(f"Preview: http://127.0.0.1:{args.port}", flush=True)
        print_accounts()
        print("Synthetic data only. MFA is " + ("required." if args.mfa else "disabled for this labelled demo."), flush=True)
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, server_header=False,
                    access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
