"""Manage team lead sign-in accounts (administrators only; run on the server machine).

    python ui/accounts.py add AG-1399              # prompts for the password twice (not echoed)
    python ui/accounts.py add AG-1399 --generate   # creates a random password and prints it once
    python ui/accounts.py remove AG-1399
    python ui/accounts.py list
    python ui/accounts.py enroll-mfa AG-1399      # service-desk provisioning; confirms a code before saving

Only active team leads get accounts here. Agents' passwords are set by their own team lead in the app
(Agent accounts page). Passwords are stored only as scrypt hashes, in warehouse/ui_accounts.json (outside git).
Share a generated password with its owner privately.
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import string
import sys
from pathlib import Path


def _staff_status(staff_id: str):
    import duckdb

    from tiui.settings import from_env
    con = duckdb.connect(str(from_env().warehouse), read_only=True)
    try:
        return con.execute("SELECT status, role FROM curated.agents WHERE agent_id = ?", [staff_id]).fetchone()
    finally:
        con.close()


def _generate() -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!@#%"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tiui.accounts import AccountStore, check_password_rules
    from tiui.settings import from_env

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="create an account or reset its password")
    add.add_argument("staff_id")
    add.add_argument("--generate", action="store_true", help="generate a random password and print it once")
    rm = sub.add_parser("remove", help="delete an account")
    rm.add_argument("staff_id")
    enroll = sub.add_parser("enroll-mfa", help="enrol or replace an authenticator after verifying the staff member")
    enroll.add_argument("staff_id")
    sub.add_parser("list", help="list staff IDs that have an account")
    a = ap.parse_args()

    store = AccountStore(from_env().accounts_file, mfa_key=from_env().mfa_key)
    if getattr(a, "staff_id", None):
        a.staff_id = a.staff_id.strip().upper()
    if a.cmd == "list":
        for sid in store.ids():
            print(sid)
        return 0
    if a.cmd == "remove":
        print("Removed." if store.remove(a.staff_id) else "No such account.")
        return 0

    row = _staff_status(a.staff_id)
    if row is None or row[0] != "active":
        print(f"{a.staff_id} is not an active member of staff; no account created.", file=sys.stderr)
        return 1
    if a.cmd == "enroll-mfa":
        import pyotp
        if row[1] not in ("agent", "senior_agent", "team_lead") or not store.info(a.staff_id):
            print("An active sign-in account is required first.", file=sys.stderr)
            return 1
        secret = pyotp.random_base32()
        print("After verifying the staff member through your service-desk process, add this key to their authenticator.")
        print(f"Issuer: TrustIssues Collections. Account: {a.staff_id}. Setup key (shown once): {secret}")
        if not pyotp.TOTP(secret).verify(getpass.getpass("Current six-digit authenticator code: ").strip(), valid_window=1):
            print("Code not verified. No account was changed.", file=sys.stderr)
            return 1
        codes = store.enroll_mfa(a.staff_id, secret)
        print("MFA enrolled. Prior sessions are invalidated. Store these single-use recovery codes privately:")
        for code in codes:
            print(code)
        return 0
    if row[1] != "team_lead":
        print(f"{a.staff_id} is a {row[1].replace('_', ' ')}. Only team leads are set up here; an agent's password is "
              "set by their team lead in the app.", file=sys.stderr)
        return 1
    if a.generate:
        password = _generate()
    else:
        password = getpass.getpass(f"New password for {a.staff_id}: ")
        problem = check_password_rules(password)
        if problem:
            print(f"Password not accepted: {problem}.", file=sys.stderr)
            return 1
        if getpass.getpass("Repeat it: ") != password:
            print("The passwords do not match.", file=sys.stderr)
            return 1
    store.set_password(a.staff_id, password)
    print(f"Team lead account ready for {a.staff_id}. They sign in with the Team lead option.")
    if a.generate:
        print(f"Password (shown once, share it privately): {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
