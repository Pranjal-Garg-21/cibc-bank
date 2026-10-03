"""Local sign-in accounts: staff ID + password, hashed with scrypt (docs/ui_design.md §9.1).

This stands in for the bank's single sign-on in the demo. There is no staff directory and nothing about a member of
staff is shown before they sign in. Accounts live in a JSON file next to the warehouse (never in git), created by an
administrator with `python ui/accounts.py add <staff id>`.

    {"AG-1304": {"hash": "<hex>", "salt": "<hex>", "n": 16384, "r": 8, "p": 1, "created": "2026-10-04T...Z"}}

Verification takes the same time whether or not the staff ID exists, and failures do not say which part was wrong.
Repeated failures lock the account for a while.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
MIN_PASSWORD = 12
MAX_PASSWORD = 128
LOCK_AFTER = 5            # failed attempts
LOCK_SECONDS = 15 * 60
_DUMMY_SALT = secrets.token_bytes(16)


def _hash(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def check_password_rules(password: str) -> str | None:
    """None if acceptable, else the reason."""
    if not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
        return f"use {MIN_PASSWORD} to {MAX_PASSWORD} characters"
    if password.isalpha() or password.isdigit():
        return "mix letters with digits or symbols"
    return None


class AccountStore:
    def __init__(self, path: Path, clock=time.monotonic):
        self.path, self.clock = path, clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    # --- file ------------------------------------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def configured(self) -> bool:
        return bool(self._load())

    def ids(self) -> list[str]:
        return sorted(self._load())

    def set_password(self, staff_id: str, password: str) -> None:
        problem = check_password_rules(password)
        if problem:
            raise ValueError(f"Password not accepted: {problem}.")
        salt = secrets.token_bytes(16)
        rec = {"hash": _hash(password, salt, **SCRYPT).hex(), "salt": salt.hex(), **SCRYPT,
               "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with self._lock:
            data = self._load()
            data[staff_id] = rec
            self._save(data)

    def remove(self, staff_id: str) -> bool:
        with self._lock:
            data = self._load()
            found = data.pop(staff_id, None) is not None
            self._save(data)
        return found

    # --- sign-in ---------------------------------------------------------------------------------------------
    def locked_for(self, staff_id: str) -> int:
        """Seconds until the account may try again (0 = not locked)."""
        now = self.clock()
        with self._lock:
            recent = [t for t in self._failures.get(staff_id, []) if now - t < LOCK_SECONDS]
            self._failures[staff_id] = recent
            if len(recent) >= LOCK_AFTER:
                return int(LOCK_SECONDS - (now - recent[-LOCK_AFTER])) + 1
            return 0

    def verify(self, staff_id: str, password: str) -> bool:
        """True only for a known staff ID with the right password. Same work either way."""
        rec = self._load().get(staff_id)
        if rec is None:
            _hash(password[:MAX_PASSWORD], _DUMMY_SALT, **SCRYPT)  # equal timing for unknown IDs
            ok = False
        else:
            got = _hash(password[:MAX_PASSWORD], bytes.fromhex(rec["salt"]), rec["n"], rec["r"], rec["p"])
            ok = hmac.compare_digest(got, bytes.fromhex(rec["hash"]))
        with self._lock:
            if ok:
                self._failures.pop(staff_id, None)
            else:
                self._failures.setdefault(staff_id, []).append(self.clock())
        return ok
