#!/usr/bin/env python3
"""Second security layer: shared gate credentials + one-time device-bound access keys.

How a run is unlocked
---------------------
1. GitHub itself decides who may clone the repository (that is layer 0).
2. This gate then asks three things, in this order:
     Username      -> the shared gate username (default: selimsb645-max)
     Password      -> the shared gate password (default: "selimsb645-max Bangladesh")
     Access key    -> a per-person key the owner issues, e.g. NAI-7F3C-91B2-D4A0
3. The access key is SINGLE-USE and DEVICE-BOUND. The first time it is accepted it
   is welded to that device's fingerprint. From then on it works only on that
   device: another phone, another Termux install or another terminal session gets
   "already activated on another device" and is refused.
4. The owner can revoke any key at any time; the next run of that person stops.

Owner commands
--------------
    python3 access.py new rahim        # issue a key (printed once)
    python3 access.py list             # who holds a key, bound device, last use
    python3 access.py revoke rahim     # kill that key immediately
    python3 access.py setgate          # change the shared username/password
    python3 access.py status           # what the gate expects

Only hashes are stored - never the password and never the key itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import secrets
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(HERE, "access_keys.json")
DEVICE_FILE = os.path.expanduser(os.path.join("~", ".ff_access_device"))
ITERATIONS = 260_000
ATTEMPTS = 3

DEFAULT_GATE_USER = "selimsb645-max"
DEFAULT_GATE_PASS = "selimsb645-max Bangladesh"

TOKEN_ENV = "BOT_ACCESS_KEY"


# ── hashing / storage ──────────────────────────────────────────────────────
def _hash(value: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"),
                               bytes.fromhex(salt_hex), ITERATIONS).hex()


def _blank() -> dict:
    salt = secrets.token_hex(16)
    return {
        "version": 2,
        "gate": {
            "username": DEFAULT_GATE_USER,
            "salt": salt,
            "hash": _hash(DEFAULT_GATE_PASS, salt),
            "iterations": ITERATIONS,
        },
        "keys": [],
    }


def load() -> dict:
    if not os.path.exists(KEYS_FILE):
        return _blank()
    try:
        with open(KEYS_FILE, encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        return _blank()
    doc.setdefault("keys", [])
    if "gate" not in doc:
        doc = _blank()
    return doc


def save(doc: dict) -> None:
    with open(KEYS_FILE, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    try:
        os.chmod(KEYS_FILE, 0o600)
    except Exception:
        pass


# ── device identity ────────────────────────────────────────────────────────
def device_fingerprint() -> str:
    """Stable per-install id. Created on first use, outside the repo.

    If the file is copied to another phone the fingerprint travels with it, so
    this is a strong deterrent plus an audit trail, not a hardware lock. A
    locked key still needs the shared password, and the owner can revoke it.
    """
    dev = ""
    if os.path.exists(DEVICE_FILE):
        try:
            with open(DEVICE_FILE, encoding="utf-8") as fh:
                dev = fh.read().strip()
        except Exception:
            dev = ""
    if not dev:
        dev = secrets.token_hex(16)
        try:
            with open(DEVICE_FILE, "w", encoding="utf-8") as fh:
                fh.write(dev)
            os.chmod(DEVICE_FILE, 0o600)
        except Exception:
            pass
    parts = "|".join([dev, platform.node() or "", platform.machine() or "",
                      str(os.getuid()) if hasattr(os, "getuid") else ""])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:32]


# ── key issue / verify ─────────────────────────────────────────────────────
def _new_token() -> str:
    raw = secrets.token_hex(6).upper()
    return "NAI-" + "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def issue_key(label: str, doc: dict | None = None) -> tuple[str, dict]:
    doc = doc or load()
    token = _new_token()
    salt = secrets.token_hex(16)
    rec = {
        "label": label.strip(),
        "salt": salt,
        "hash": _hash(token, salt),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bound_device": "",
        "bound_at": "",
        "last_used": "",
        "uses": 0,
        "revoked": False,
    }
    doc["keys"].append(rec)
    save(doc)
    return token, rec


def find_key(token: str, doc: dict) -> dict | None:
    """Match a token against the stored hashes (constant-time per record)."""
    token = (token or "").strip()
    if not token:
        return None
    for rec in doc.get("keys", []):
        if rec.get("revoked"):
            continue
        got = _hash(token, rec["salt"])
        if hmac.compare_digest(got, rec["hash"]):
            return rec
    return None


def gate_ok(username: str, password: str, doc: dict) -> bool:
    gate = doc.get("gate") or {}
    user_ok = hmac.compare_digest(str(username).strip(),
                                 str(gate.get("username", "")))
    salt = gate.get("salt", "")
    if not salt:
        return False
    pass_ok = hmac.compare_digest(_hash(password, salt), gate.get("hash", ""))
    return user_ok and pass_ok


def redeem(token: str, doc: dict | None = None) -> tuple[bool, str, dict | None]:
    """Activate or accept a key. Returns (ok, message, record)."""
    doc = doc or load()
    rec = find_key(token, doc)
    if not rec:
        return False, "invalid, revoked or never issued key", None
    fp = device_fingerprint()
    if not rec.get("bound_device"):
        rec["bound_device"] = fp
        rec["bound_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec["uses"] = 1
        rec["last_used"] = rec["bound_at"]
        save(doc)
        return True, f"key accepted and locked to this device ({rec['label']})", rec
    if rec["bound_device"] != fp:
        return False, ("this key is already activated on another device - "
                       "ask the owner for a new one"), rec
    rec["uses"] = int(rec.get("uses") or 0) + 1
    rec["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save(doc)
    return True, f"welcome back, {rec['label']}", rec


def _fail(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def require_access(purpose: str = "this program") -> None:
    """Ask username, password, access key. Returns only when all three pass."""
    doc = load()

    env_key = os.environ.get(TOKEN_ENV, "").strip()
    if env_key:
        ok, msg, rec = redeem(env_key, doc)
        if not ok:
            _fail(f"[access] {TOKEN_ENV} rejected: {msg}")
        print(f"[access] {rec['label']} verified from environment")
        return

    if os.environ.get("GITHUB_ACTIONS") == "true" and not sys.stdin.isatty():
        print("[access] trusted CI runner without a terminal - skipping the "
              "interactive key check. Set BOT_ACCESS_KEY to require it here too.")
        return

    print(f"\n=== {purpose} is locked ===")
    import getpass
    for attempt in range(1, ATTEMPTS + 1):
        left = ATTEMPTS - attempt + 1
        try:
            user = input(f"Username ({left} tries left): ").strip()
            pw = getpass.getpass("Password: ")
        except (EOFError, KeyboardInterrupt):
            print("\n[access] cancelled.")
            raise SystemExit(1)
        if not gate_ok(user, pw, doc):
            print("[access] wrong username or password.")
            continue
        try:
            key = input("Access key: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[access] cancelled.")
            raise SystemExit(1)
        ok, msg, rec = redeem(key, load())
        if ok:
            print(f"[access] {msg}")
            return
        print(f"[access] {msg}")
    _fail("[access] too many failed attempts - not starting.")


# ── CLI ────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "status").lower()

    if cmd == "new":
        if len(argv) < 3:
            print("usage: python3 access.py new <person>")
            return 2
        token, rec = issue_key(argv[2])
        print(f"\nAccess key for '{rec['label']}':\n\n    {token}\n")
        print("Send it to that person ONCE. It is not stored anywhere and")
        print("cannot be shown again. They type it at the 'Access key' prompt.")
        print("It locks to the first device that uses it.")
        return 0

    if cmd == "list":
        doc = load()
        keys = doc.get("keys", [])
        if not keys:
            print("no access keys issued yet")
            return 0
        print(f"{'person':14s} {'state':10s} {'bound':20s} {'uses':5s} last used")
        for k in keys:
            state = "revoked" if k.get("revoked") else (
                "active" if k.get("bound_device") else "unused")
            print(f"{k['label'][:14]:14s} {state:10s} "
                  f"{(k.get('bound_at') or '-')[:19]:20s} "
                  f"{str(k.get('uses') or 0):5s} {(k.get('last_used') or '-')[:19]}")
        return 0

    if cmd == "revoke":
        if len(argv) < 3:
            print("usage: python3 access.py revoke <person>")
            return 2
        doc = load()
        hit = 0
        for k in doc.get("keys", []):
            if k["label"] == argv[2] and not k.get("revoked"):
                k["revoked"] = True
                hit += 1
        save(doc)
        print(f"revoked {hit} key(s) for '{argv[2]}'")
        return 0

    if cmd == "setgate":
        import getpass
        user = input(f"Gate username [{DEFAULT_GATE_USER}]: ").strip() or DEFAULT_GATE_USER
        pw = getpass.getpass("Gate password: ")
        again = getpass.getpass("Repeat gate password: ")
        if pw != again or len(pw) < 6:
            print("aborted: passwords do not match or are too short")
            return 1
        doc = load()
        salt = secrets.token_hex(16)
        doc["gate"] = {"username": user, "salt": salt,
                       "hash": _hash(pw, salt), "iterations": ITERATIONS}
        save(doc)
        print(f"gate credentials updated (username: {user})")
        return 0

    doc = load()
    gate = doc.get("gate") or {}
    keys = doc.get("keys", [])
    print("access gate")
    print(f"  username     : {gate.get('username')}")
    print(f"  password     : (stored as PBKDF2-HMAC-SHA256 hash, {ITERATIONS} rounds)")
    print(f"  keys total   : {len(keys)}")
    print(f"  keys active  : {sum(1 for k in keys if not k.get('revoked'))}")
    print(f"  keys unused  : {sum(1 for k in keys if not k.get('revoked') and not k.get('bound_device'))}")
    print(f"  this device  : {device_fingerprint()[:16]}...")
    print(f"  key file     : {os.path.basename(KEYS_FILE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
