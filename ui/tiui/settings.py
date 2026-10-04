"""UI settings, read once from the environment. Sign-in works only for accounts an administrator created
(ui/accounts.py); with no accounts file, nobody can sign in."""
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
    demo: bool = False                # UI_DEMO: show the DEMO ENVIRONMENT tag (synthetic data, local accounts)
    dev: bool = False                 # UI_DEV: OpenAPI docs at /api/docs
    https: bool = False               # UI_HTTPS: served behind TLS -> Secure cookie + HSTS
    ask_llm: bool = False             # UI_ASK_LLM: let Ask use the language model and policy search
    idle_minutes: int = 15
    max_hours: int = 8
    session_timeouts: bool = True     # UI_SESSION_TIMEOUTS; demo-only exception
    require_mfa: bool = True          # may be disabled ONLY in a labelled synthetic-data demo
    mfa_key: str | None = field(default=None, repr=False)  # UI_MFA_KEY: secret-manager Fernet key
    demo_password: str | None = field(default=None, repr=False)  # temporary shared access, never persisted
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
    identity_reviewer_roles: tuple[str, ...] = ("team_lead", "qa_analyst")
    warehouse: Path = field(default_factory=config.warehouse_path)
    ui_audit: Path = field(default_factory=lambda: config.warehouse_path().parent / "ui_audit.duckdb")
    accounts_file: Path = field(default_factory=lambda: config.warehouse_path().parent / "ui_accounts.json")
    decisions_csv: Path = config.REPO_ROOT / "data" / "review" / "identity_decisions.csv"
    reports_dir: Path = config.REPO_ROOT / "reports"
    dist_dir: Path = Path(__file__).resolve().parents[1] / "web" / "dist"

    def __post_init__(self):
        if not self.session_timeouts and not self.demo:
            raise ValueError("Automatic session expiry may be disabled only in a labelled demo.")
        if self.demo_password is not None and (not self.demo or self.require_mfa):
            raise ValueError("Shared access requires an explicitly labelled demo with MFA disabled.")
        if self.demo_password is not None and (not self.demo_password or any(
                h not in ("localhost", "127.0.0.1", "::1", "testserver") for h in self.allowed_hosts)):
            raise ValueError("Shared demo access requires a password and loopback hosts only.")
        if not self.require_mfa and not self.demo:
            raise ValueError("MFA may be disabled only with UI_DEMO=1 and synthetic data.")
        idle_max = 120 if self.demo else 30
        if not 1 <= self.idle_minutes <= idle_max or not 1 <= self.max_hours <= 12:
            raise ValueError(f"Session limits must be 1-{idle_max} idle minutes and 1-12 absolute hours.")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("Set an explicit list of UI_ALLOWED_HOSTS; wildcards are not accepted.")


@lru_cache(maxsize=1)
def from_env() -> Settings:
    return Settings(
        demo=_flag("UI_DEMO"),
        dev=_flag("UI_DEV"),
        https=_flag("UI_HTTPS"),
        ask_llm=_flag("UI_ASK_LLM"),
        idle_minutes=int(os.environ.get("UI_SESSION_IDLE_MIN", 15)),
        max_hours=int(os.environ.get("UI_SESSION_MAX_HOURS", 8)),
        session_timeouts=_flag("UI_SESSION_TIMEOUTS", True),
        require_mfa=_flag("UI_REQUIRE_MFA", True),
        mfa_key=os.environ.get("UI_MFA_KEY"),
        demo_password=os.environ.get("UI_DEMO_PASSWORD"),
        allowed_hosts=_roles("UI_ALLOWED_HOSTS", "localhost,127.0.0.1,::1"),
        identity_reviewer_roles=_roles("UI_IDENTITY_REVIEWER_ROLES", "team_lead,qa_analyst"),
        **({"accounts_file": Path(os.environ["UI_ACCOUNTS_FILE"])} if os.environ.get("UI_ACCOUNTS_FILE") else {}),
    )
