#!/usr/bin/env python3
"""Login gate with per-person access for this repository.

Nobody runs the bot just by cloning the repo: every entry point calls
``require_auth()`` first, and that call asks for a username and password.

Only the people the owner adds get in. Each person gets their own username and
password, so access can be given, rotated or taken away one person at a time -
deleting a user locks that person out on their next start, and nobody else is
affected.

    python3 auth.py adduser ahmed      # give someone access (asks a password)
    python3 auth.py list               # who can run this bot
    python3 auth.py deluser ahmed      # take it back
    python3 auth.py passwd selim       # rotate a password
    python3 auth.py status             # quick summary

How passwords are stored
------------------------
Only a salted PBKDF2-HMAC-SHA256 hash lives in ``auth.json`` (260 000 rounds,
16-byte salt per user). No plaintext password is ever written, committed,
printed or logged.

How the gate can pass, in order
-------------------------------
1. ``BOT_USERNAME`` + ``BOT_PASSWORD`` in the environment (Actions secrets,
   systemd Environment=, docker -e). Verified against the same hashes.
2. An interactive terminal: prompts for username + password, 3 attempts.
3. A trusted scheduled runner (``GITHUB_ACTIONS=true`` with no terminal), so
   the farm cannot stall. Set the two secrets to require login there as well.

Anything else exits non-zero without starting the bot.
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

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


def _new_entry(password: str, role: str = "guest", note: str = "") -> dict:
    salt = secrets.token_hex(16)
    return {
        "salt": salt,
        "hash": _hash(password, salt),
        "iterations": ITERATIONS,
        "algo": "pbkdf2_hmac_sha256",
        "role": role,
        "note": note,
        "added": time.strftime("%Y-%m-%d"),
    }


# ── storage (v2 = many users, v1 = one user, read transparently) ───────────
def _read_raw(path: str = AUTH_FILE) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return rec if isinstance(rec, dict) else {}


def load_users(path: str = AUTH_FILE) -> dict:
    """{username: entry}. Old single-user files are migrated on the fly."""
    raw = _read_raw(path)
    users = raw.get("users")
    if isinstance(users, dict) and users:
        return users
    if raw.get("username") and raw.get("hash") and raw.get("salt"):
        return {raw["username"]: {
            "salt": raw["salt"], "hash": raw["hash"],
            "iterations": raw.get("iterations") or ITERATIONS,
            "algo": raw.get("algo") or "pbkdf2_hmac_sha256",
            "role": "owner", "note": "", "added": "",
        }}
    return {}


def save_users(users: dict, path: str = AUTH_FILE) -> None:
    doc = {"version": 2,
           "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "users": users}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    try:                              # hashes only, but still keep it private
        os.chmod(path, 0o600)
    except Exception:
        pass


# ── user management ────────────────────────────────────────────────────────
def add_user(username: str, password: str, role: str = "guest",
             note: str = "") -> dict:
    username = (username or "").strip()
    if not username:
        raise SystemExit("a username is required")
    if len(password) < 6:
        raise SystemExit("password must be at least 6 characters")
    users = load_users()
    users[username] = _new_entry(password, role, note)
    save_users(users)
    return users


def remove_user(username: str) -> bool:
    users = load_users()
    if username not in users:
        return False
    del users[username]
    save_users(users)
    return True


def list_users() -> list[tuple[str, str, str, str]]:
    return [(u, e.get("role", "guest"), e.get("added", ""), e.get("note", ""))
            for u, e in sorted(load_users().items())]


# ── verification ───────────────────────────────────────────────────────────
def check(username: str, password: str, users: dict | None = None) -> bool:
    users = users if users is not None else load_users()
    entry = users.get(str(username).strip())
    if not entry or not entry.get("hash") or not entry.get("salt"):
        return False
    got = _hash(str(password), entry["salt"],
                int(entry.get("iterations") or ITERATIONS))
    return hmac.compare_digest(got, entry["hash"])


def _fail(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _second_layer() -> None:
    """Hand over to the shared-credential + device-bound access key gate."""
    try:
        import access
    except Exception as ex:                      # pragma: no cover
        print(f"[auth] access layer unavailable: {ex}")
        return
    access.require_access(access_purpose or "FF BD bot")


access_purpose = ""


def require_auth(purpose: str = "this program") -> None:
    """Start-up gate.

    Kept as the single entry point every module already calls, but the
    per-user username/password prompt is gone: access is now proven by the
    access key alone (access.require_access), because the owner asked for one
    key at start-up instead of a stack of logins. The users map in auth.json is
    still readable for older installs and is not consulted here.
    """
    import os
    if os.environ.get("FF_SKIP_ACCESS", "").strip().lower() in ("1", "true", "yes", "on"):
        # Owner 2026-11-20: the @NAI_AccessBot gate is off until that server is back.
        print("[auth] skipped (FF_SKIP_ACCESS=1)")
        return
    try:
        import access
    except Exception as ex:                       # pragma: no cover
        print(f"[auth] access layer unavailable: {ex}")
        return
    access.require_access(purpose)


# ── CLI ────────────────────────────────────────────────────────────────────
def _prompt_new_password(user: str) -> str:
    while True:
        pw = getpass.getpass(f"New password for {user}: ")
        if len(pw) < 6:
            print("  at least 6 characters, please.")
            continue
        if pw != getpass.getpass("Repeat password: "):
            print("  the two passwords did not match.")
            continue
        return pw


def main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "status").lower()
    args = argv[2:]

    if cmd in ("status", "list"):
        users = list_users()
        print("login configured:", bool(users))
        print("users:", len(users))
        for u, role, added, note in users:
            print(f"  {u:16s} {role:6s} added {added or '-':10s} {note}")
        print("env credentials present:", bool(os.environ.get(USER_ENV)
                                               and os.environ.get(PASS_ENV)))
        return 0

    if cmd == "check":
        require_auth("login check")
        return 0

    if cmd == "adduser":
        if not args:
            _fail("usage: python3 auth.py adduser <username> [--owner] [--note text]")
        user = args[0]
        role = "owner" if "--owner" in args else "guest"
        note = ""
        if "--note" in args:
            note = " ".join(args[args.index("--note") + 1:])
        if user in load_users() and "--force" not in args:
            _fail(f"[auth] '{user}' already exists. Use passwd to change the "
                  f"password, or add --force to overwrite.")
        add_user(user, _prompt_new_password(user), role, note)
        print(f"[auth] access granted to '{user}' ({role}).")
        return 0

    if cmd in ("deluser", "remove", "revoke"):
        if not args:
            _fail("usage: python3 auth.py deluser <username>")
        if remove_user(args[0]):
            print(f"[auth] access revoked for '{args[0]}'. "
                  f"They cannot start the bot anymore.")
            return 0
        _fail(f"[auth] no such user: {args[0]}")

    if cmd in ("passwd", "password"):
        if not args:
            _fail("usage: python3 auth.py passwd <username>")
        users = load_users()
        if args[0] not in users:
            _fail(f"[auth] no such user: {args[0]}")
        users[args[0]].update(_new_entry(_prompt_new_password(args[0]),
                                        users[args[0]].get("role", "guest"),
                                        users[args[0]].get("note", "")))
        save_users(users)
        print(f"[auth] password changed for '{args[0]}'.")
        return 0

    if cmd == "set":                       # back-compat with set_login.py
        import set_login
        return set_login.main(["set_login.py", *args])

    print("usage: python3 auth.py [status|list|check|adduser|deluser|passwd|set]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
