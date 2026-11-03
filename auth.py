#!/usr/bin/env python3
"""Login gate for this repository.

Nobody runs the bot just by cloning the repo: every entry point calls
``require_auth()`` first and that call asks for the username + password.

How the password is stored
--------------------------
Only a salted PBKDF2-HMAC-SHA256 hash lives in ``auth.json`` (260 000 rounds).
The plaintext password is never written to disk, never committed and never
printed. ``set_login.py`` writes/rotates the record.

Three ways the gate can pass, in order
--------------------------------------
1. ``BOT_USERNAME`` + ``BOT_PASSWORD`` in the environment (GitHub Actions
   secrets, systemd Environment=, docker -e ...). Verified against the hash.
2. An interactive terminal: prompts for username and password, 3 attempts.
3. A trusted CI runner (``GITHUB_ACTIONS=true``) when no credentials are
   configured yet - the scheduled farm must not stall on a prompt. Set the two
   secrets to close this door as well.

Any other case exits non-zero without starting the bot.
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.path.join(HERE, "auth.json")
ITERATIONS = 260_000
ATTEMPTS = 3

USER_ENV = "BOT_USERNAME"
PASS_ENV = "BOT_PASSWORD"


# ── hashing ────────────────────────────────────────────────────────────────
def _hash(password: str, salt_hex: str, iterations: int = ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations
    ).hex()


def load_record(path: str = AUTH_FILE) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return rec if isinstance(rec, dict) else {}


def save_record(username: str, password: str, path: str = AUTH_FILE) -> dict:
    """Write a fresh salt + hash for ``username``. Returns the stored record."""
    salt = secrets.token_hex(16)
    rec = {
        "username": username.strip(),
        "salt": salt,
        "hash": _hash(password, salt),
        "iterations": ITERATIONS,
        "algo": "pbkdf2_hmac_sha256",
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    try:                                  # hash file, not world readable
        os.chmod(path, 0o600)
    except Exception:
        pass
    return rec


# ── verification ───────────────────────────────────────────────────────────
def check(username: str, password: str, record: dict | None = None) -> bool:
    rec = record if record is not None else load_record()
    if not rec or not rec.get("hash") or not rec.get("salt"):
        return False
    user_ok = hmac.compare_digest(str(username).strip(), str(rec.get("username", "")))
    got = _hash(str(password), rec["salt"], int(rec.get("iterations") or ITERATIONS))
    pass_ok = hmac.compare_digest(got, rec["hash"])
    return user_ok and pass_ok


def _fail(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def require_auth(purpose: str = "this program") -> None:
    """Gate the process. Returns only when the caller is authenticated."""
    rec = load_record()

    if not rec:
        # No credential has been set yet: refuse unless this is the trusted
        # automation runner, so a fresh clone cannot simply start the bot.
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("[auth] no local credential record; trusted CI runner - "
                  "continuing. Run set_login.py to lock this repo.")
            return
        _fail("[auth] no login configured for this repository.\n"
              "       Owner: run  python3 set_login.py  once to create it.")

    env_user = os.environ.get(USER_ENV, "").strip()
    env_pass = os.environ.get(PASS_ENV, "")
    if env_user and env_pass:
        if check(env_user, env_pass, rec):
            print(f"[auth] {env_user} verified from environment")
            return
        _fail("[auth] BOT_USERNAME / BOT_PASSWORD are wrong. Refusing to start.")

    # A scheduled farm cannot type a password. Keep it running, but say so.
    if os.environ.get("GITHUB_ACTIONS") == "true" and not sys.stdin.isatty():
        print("[auth] trusted CI runner (no prompt possible) - continuing. "
              "Set BOT_USERNAME/BOT_PASSWORD secrets to require login here too.")
        return

    print(f"\n=== {purpose} is locked ===")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            user = input(f"Username ({ATTEMPTS - attempt + 1} tries left): ").strip()
            pw = getpass.getpass("Password: ")
        except (EOFError, KeyboardInterrupt):
            _fail("\n[auth] cancelled.")
        if check(user, pw, rec):
            print(f"[auth] welcome, {user}\n")
            return
        print("[auth] wrong username or password.")
    _fail("[auth] too many failed attempts. Exiting.")


def main(argv: list[str]) -> int:
    """`python3 auth.py status|check|set` for quick shell use."""
    cmd = argv[1] if len(argv) > 1 else "status"
    rec = load_record()
    if cmd == "status":
        print("configured:", bool(rec))
        print("username:", rec.get("username", "-"))
        print("hash:", rec.get("algo", "-"), rec.get("iterations", "-"), "rounds")
        print("env credentials present:", bool(os.environ.get(USER_ENV)
                                               and os.environ.get(PASS_ENV)))
        return 0
    if cmd == "check":
        require_auth("login check")
        return 0
    if cmd == "set":
        import set_login
        return set_login.main(argv)
    print("usage: python3 auth.py [status|check|set]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
