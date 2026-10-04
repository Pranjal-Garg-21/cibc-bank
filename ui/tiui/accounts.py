"""Local sign-in accounts: staff ID + password, hashed with scrypt (docs/ui_design.md §9.1).

This stands in for the bank's single sign-on in the demo. There is no staff directory and nothing about a member of
staff is shown before they sign in. Accounts live in a JSON file next to the warehouse (never in git):
team lead accounts are created by an administrator (`python ui/accounts.py add <staff id>`); an agent's password is
set by their team lead in the app and the agent cannot change it.

    {"AG-1304": {"hash": "<hex>", "salt": "<hex>", "n": 16384, "r": 8, "p": 1, "created": "2026-10-04T...Z",
                 "set_by": "AG-1399" | "admin"}}

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
import tempfile
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

import pyotp
from cryptography.fernet import Fernet
from filelock import FileLock

SCRYPT = {"n": 2 ** 17, "r": 8, "p": 1}
LEGACY_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
MIN_PASSWORD = 12
MAX_PASSWORD = 128
LOCK_AFTER = 5            # failed attempts
LOCK_SECONDS = 15 * 60
_DUMMY_SALT = secrets.token_bytes(16)
_COMMON_PASSWORDS = frozenset({"password1234", "password123!", "password123456", "password123456!",
                              "password12345", "qwerty123456", "qwerty123456!", "welcome12345",
                              "welcome12345!", "welcome123456", "changeme12345", "changeme12345!",
                              "123456789012", "letmein123456", "trustissues123", "cibcpassword123"})


def _hash(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=32)


def check_password_rules(password: str) -> str | None:
    """None if acceptable, else the reason."""
    if not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
        return f"use {MIN_PASSWORD} to {MAX_PASSWORD} characters"
    if password.isalpha() or password.isdigit():
        return "mix letters with digits or symbols"
    if password.strip().casefold() in _COMMON_PASSWORDS:
        return "choose a password that is not commonly used"
    return None


class AccountStore:
    def __init__(self, path: Path, clock=time.monotonic, mfa_key: str | None = None, wall_clock=time.time):
        self.path, self.clock = path, clock
        self.mfa_key, self.wall_clock = mfa_key, wall_clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    @contextmanager
    def _transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, FileLock(str(self.path) + ".lock", timeout=10):
            yield

    # --- file ------------------------------------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=1, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)  # temp file and resulting account file are owner-only (0600)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _fernet(self, create: bool = False) -> Fernet:
        if self.mfa_key:
            return Fernet(self.mfa_key.encode("ascii"))
        path = self.path.with_suffix(".mfa.key")
        if create and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(Fernet.generate_key())
        return Fernet(path.read_bytes().strip())

    def version(self, staff_id: str) -> str | None:
        rec = self._load().get(staff_id)
        return rec.get("version", rec["hash"]) if rec else None

    def has_mfa(self, staff_id: str) -> bool:
        return bool(self._load().get(staff_id, {}).get("mfa_secret"))

    def enroll_mfa(self, staff_id: str, secret: str) -> list[str]:
        """Administrator-only provisioning. Replacement rotates the credential version and ends old sessions."""
        codes = [secrets.token_hex(16) for _ in range(10)]
        with self._transaction():
            data = self._load()
            if staff_id not in data:
                raise ValueError("Create the password account first.")
            rec = data[staff_id]
            rec.update(mfa_secret=self._fernet(create=True).encrypt(secret.encode("ascii")).decode("ascii"),
                       mfa_last_counter=-1, recovery_hashes=[hashlib.sha256(c.encode()).hexdigest() for c in codes],
                       version=secrets.token_hex(16))
            self._save(data)
        return codes

    def verify_mfa(self, staff_id: str, code: str) -> bool:
        """Consume an OTP counter or recovery code once, atomically. No plaintext secrets in the account file."""
        code = code.strip().replace(" ", "")
        with self._transaction():
            data = self._load()
            rec = data.get(staff_id)
            if not rec or not rec.get("mfa_secret"):
                return False
            if len(code) == 32:
                got = hashlib.sha256(code.casefold().encode()).hexdigest()
                match = next((v for v in rec.get("recovery_hashes", []) if hmac.compare_digest(got, v)), None)
                if not match:
                    return False
                rec["recovery_hashes"].remove(match)
            elif len(code) == 6 and code.isascii() and code.isdigit():
                otp = pyotp.TOTP(self._fernet().decrypt(rec["mfa_secret"].encode()).decode("ascii"))
                counter = int(self.wall_clock()) // otp.interval
                matched = next((n for n in (counter, counter - 1, counter + 1)
                                if n > rec.get("mfa_last_counter", -1)
                                and hmac.compare_digest(otp.at(n * otp.interval), code)), None)
                if matched is None:
                    return False
                rec["mfa_last_counter"] = matched
            else:
                return False
            self._save(data)
            return True

    def configured(self) -> bool:
        return bool(self._load())

    def ids(self) -> list[str]:
        return sorted(self._load())

    def info(self, staff_id: str) -> dict | None:
        """When and by whom the password was set. Never the hash."""
        rec = self._load().get(staff_id)
        return {"set_at": rec.get("created"), "set_by": rec.get("set_by")} if rec else None

    def all_info(self) -> dict[str, dict]:
        return {sid: {"set_at": r.get("created"), "set_by": r.get("set_by")} for sid, r in self._load().items()}

    def set_password(self, staff_id: str, password: str, set_by: str = "admin") -> None:
        problem = check_password_rules(password)
        if problem:
            raise ValueError(f"Password not accepted: {problem}.")
        salt = secrets.token_bytes(16)
        rec = {"hash": _hash(password, salt, **SCRYPT).hex(), "salt": salt.hex(), **SCRYPT,
               "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "set_by": set_by,
               "version": secrets.token_hex(16)}
        with self._transaction():
            data = self._load()
            # Resetting a password must never remove or replace the owner's existing second factor.
            old = data.get(staff_id, {})
            rec.update({k: v for k, v in old.items() if k.startswith("mfa_") or k in ("recovery_hashes", "last_login")})
            data[staff_id] = rec
            self._save(data)

    def remove(self, staff_id: str) -> bool:
        with self._transaction():
            data = self._load()
            found = data.pop(staff_id, None) is not None
            self._save(data)
            self._failures.pop(staff_id, None)
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

    def record_attempt(self, staff_id: str, ok: bool) -> None:
        with self._lock:
            if ok:
                self._failures.pop(staff_id, None)
            else:
                now = self.clock()
                recent = [t for t in self._failures.get(staff_id, []) if now - t < LOCK_SECONDS]
                self._failures[staff_id] = (recent + [now])[-LOCK_AFTER:]

    def verify(self, staff_id: str, password: str, record: bool = True) -> bool:
        """True only for a known staff ID with the right password. Same work either way."""
        rec = self._load().get(staff_id)
        if rec is None:
            _hash(password[:MAX_PASSWORD], _DUMMY_SALT, **SCRYPT)  # equal timing for unknown IDs
            _hash(password[:MAX_PASSWORD], _DUMMY_SALT, **LEGACY_SCRYPT)
            ok = False
        else:
            got = _hash(password[:MAX_PASSWORD], bytes.fromhex(rec["salt"]), rec["n"], rec["r"], rec["p"])
            # Equalise work across existing v1 accounts and new, stronger hashes; no ID enumeration by KDF cost.
            padding = SCRYPT if rec["n"] == LEGACY_SCRYPT["n"] else LEGACY_SCRYPT
            _hash(password[:MAX_PASSWORD], _DUMMY_SALT, **padding)
            ok = hmac.compare_digest(got, bytes.fromhex(rec["hash"]))
        if record:
            self.record_attempt(staff_id, ok)
        return ok


class SharedDemoAccounts(AccountStore):
    """One in-memory demo credential. Staff eligibility and roles always come from DuckDB.

    No per-person credentials are read, changed or written. Account management is disabled by the router.
    """

    def __init__(self, password: str):
        super().__init__(Path("unused-demo-accounts"))
        self._salt = secrets.token_bytes(16)
        self._digest = _hash(password, self._salt, **SCRYPT)
        self._version = secrets.token_hex(16)

    def _load(self) -> dict:
        return {}

    def _save(self, data: dict) -> None:
        raise RuntimeError("Shared demo access does not persist accounts.")

    def configured(self) -> bool:
        return True

    def version(self, staff_id: str) -> str:
        return self._version

    def verify(self, staff_id: str, password: str, record: bool = True) -> bool:
        ok = hmac.compare_digest(_hash(password[:MAX_PASSWORD], self._salt, **SCRYPT), self._digest)
        if record:
            self.record_attempt(staff_id, ok)
        return ok
