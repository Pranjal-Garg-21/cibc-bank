"""Start the UI server (API + built React app) on this machine only. Works the same on Windows, macOS and Linux.

    python ui/serve.py            # sign-in disabled (safe default)
    python ui/serve.py --demo     # demo staff picker, shows the DEMO tag
    python ui/serve.py --demo --dev   # also accept the Vite dev server (npm run dev) and serve /api/docs

Build the app first: cd ui/web && npm ci && npm run build
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="enable the demo staff picker (UI_DEMO_LOGIN=1)")
    ap.add_argument("--dev", action="store_true", help="development mode (UI_DEV=1)")
    ap.add_argument("--ask-llm", action="store_true", help="let Ask use the language model (UI_ASK_LLM=1)")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    if a.demo:
        os.environ["UI_DEMO_LOGIN"] = "1"
    if a.dev:
        os.environ["UI_DEV"] = "1"
    if a.ask_llm:
        os.environ["UI_ASK_LLM"] = "1"
    ui_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(ui_dir))
    if not (ui_dir / "web" / "dist" / "index.html").is_file():
        print("Note: ui/web/dist is missing, so only the API is served. Build it with: cd ui/web && npm run build")
    import uvicorn
    # 127.0.0.1 only: exposing the UI on a network needs a TLS reverse proxy (docs/ui_design.md §9.3)
    uvicorn.run("tiui.main:app", host="127.0.0.1", port=a.port, server_header=False, log_level="info")


if __name__ == "__main__":
    main()
