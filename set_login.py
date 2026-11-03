#!/usr/bin/env python3
"""Create or change the login this repository asks for on every start.

    python3 set_login.py                 # asks for username + password
    python3 set_login.py --user selim    # asks only for the password

The password is never stored in plain text: auth.json keeps a salted
PBKDF2-HMAC-SHA256 hash (260 000 rounds) plus the username.

To change it later, just run this again. To use the bot from a server or from
GitHub Actions instead of typing it, set BOT_USERNAME and BOT_PASSWORD in the
environment (repo secrets) and the same hash verifies them automatically.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

import auth

MIN_LEN = 8


def _ask_password() -> str:
    for _ in range(3):
        pw = getpass.getpass("New password (min 8 chars): ")
        if len(pw) < MIN_LEN:
            print(f"  too short - needs at least {MIN_LEN} characters")
            continue
        again = getpass.getpass("Repeat password: ")
        if pw != again:
            print("  the two passwords do not match")
            continue
        return pw
    print("aborted: no usable password entered")
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="set the login for this repository")
    ap.add_argument("--user", default="", help="username (asked for when empty)")
    args = ap.parse_args(argv)

    user = (args.user or "").strip()
    if not user:
        user = input("Username: ").strip()
    if not user:
        print("aborted: username cannot be empty")
        return 1

    pw = os.environ.get("BOT_PASSWORD_NEW") or _ask_password()
    rec = auth.save_record(user, pw)
    print(f"\nlogin saved to {os.path.basename(auth.AUTH_FILE)}")
    print(f"  username: {rec['username']}")
    print(f"  hash:     {rec['algo']} x{rec['iterations']}")
    print("\nThe bot will now ask for this username and password when it starts.")
    print("Keep the password yourself - it cannot be recovered from the hash.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
