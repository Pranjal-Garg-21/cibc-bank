"""UI settings, read once from the environment. Safe defaults: with nothing set, nobody can sign in."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from trustissues.common import config


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def _roles(name: str, default: str) -> tuple[str, ...]:
    return tuple(r.strip() for r in os.environ.get(name, default).split(",") if r.strip())


@dataclass(frozen=True)
class Settings:
    demo_login: bool = False          # UI_DEMO_LOGIN: staff picker instead of SSO (demo only)
    dev: bool = False                 # UI_DEV: OpenAPI docs at /api/docs
    https: bool = False               # UI_HTTPS: served behind TLS -> Secure cookie + HSTS
    ask_llm: bool = False             # UI_ASK_LLM: let Ask use the language model and policy search
    idle_minutes: int = 15
    max_hours: int = 8
    identity_reviewer_roles: tuple[str, ...] = ("qa_analyst",)
    warehouse: Path = field(default_factory=config.warehouse_path)
    ui_audit: Path = field(default_factory=lambda: config.warehouse_path().parent / "ui_audit.duckdb")
    decisions_csv: Path = config.REPO_ROOT / "data" / "review" / "identity_decisions.csv"
    reports_dir: Path = config.REPO_ROOT / "reports"
    dist_dir: Path = Path(__file__).resolve().parents[1] / "web" / "dist"


@lru_cache(maxsize=1)
def from_env() -> Settings:
    return Settings(
        demo_login=_flag("UI_DEMO_LOGIN"),
        dev=_flag("UI_DEV"),
        https=_flag("UI_HTTPS"),
        ask_llm=_flag("UI_ASK_LLM"),
        idle_minutes=int(os.environ.get("UI_SESSION_IDLE_MIN", 15)),
        max_hours=int(os.environ.get("UI_SESSION_MAX_HOURS", 8)),
        identity_reviewer_roles=_roles("UI_IDENTITY_REVIEWER_ROLES", "qa_analyst"),
    )
