"""Start the UI server (API + built React app) on this machine only. Works the same on Windows, macOS and Linux.

    python ui/serve.py                 # staff sign in with the accounts created by ui/accounts.py
    python ui/serve.py --demo          # same, with the DEMO ENVIRONMENT tag shown
    python ui/serve.py --demo --dev    # also accept the Vite dev server (npm run dev) and serve /api/docs

Build the app first: cd ui/web && npm ci && npm run build
Create a sign-in account: python ui/accounts.py add <staff id>
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="show the DEMO ENVIRONMENT tag (UI_DEMO=1)")
    ap.add_argument("--shared-access", action="store_true", help="temporary shared demo password; no per-person accounts written")
    ap.add_argument("--dev", action="store_true", help="development mode (UI_DEV=1)")
    ap.add_argument("--ask-llm", action="store_true", help="let Ask use the language model (UI_ASK_LLM=1)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--accounts-file", help="sign-in accounts file (default: warehouse/ui_accounts.json)")
    a = ap.parse_args()
    if a.demo:
        os.environ["UI_DEMO"] = "1"
    if a.shared_access:
        if not a.demo:
            ap.error("--shared-access requires --demo")
        password = getpass.getpass("Temporary shared demo password (not saved): ")
        if not password:
            ap.error("A non-empty demo password is required")
        os.environ["UI_DEMO_PASSWORD"] = password
        os.environ["UI_REQUIRE_MFA"] = "0"
        os.environ.setdefault("UI_SESSION_TIMEOUTS", "1")
        os.environ.setdefault("UI_SESSION_IDLE_MIN", "60")
        os.environ.setdefault("UI_SESSION_MAX_HOURS", "1")
    if a.dev:
        os.environ["UI_DEV"] = "1"
    if a.ask_llm:
        os.environ["UI_ASK_LLM"] = "1"
    if a.accounts_file:
        os.environ["UI_ACCOUNTS_FILE"] = str(Path(a.accounts_file).resolve())
    ui_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(ui_dir))
    from tiui.accounts import AccountStore
    from tiui.settings import from_env
    if from_env().demo_password is None and not AccountStore(from_env().accounts_file).configured():
        print("Note: no sign-in accounts yet. Create one with: python ui/accounts.py add <staff id>")
    if not (ui_dir / "web" / "dist" / "index.html").is_file():
        print("Note: ui/web/dist is missing, so only the API is served. Build it with: cd ui/web && npm run build")
    import uvicorn
    # 127.0.0.1 only: exposing the UI on a network needs a TLS reverse proxy (docs/ui_design.md §9.3)
    uvicorn.run("tiui.main:app", host="127.0.0.1", port=a.port, server_header=False, log_level="info")


if __name__ == "__main__":
    main()
