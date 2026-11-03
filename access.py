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

# Telegram access bot (functions/ffAccessBot in the agent app).
# It signs short codes with this secret; we verify them offline, so the terminal
# never needs to call a server to be let in.
TG_BOT = "NAI_AccessBot"
SIGN_SECRET = b"NAI-access-2026-q7Lm4xZ9pT2vRb6n"


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
        # signed-code records carry code_fp instead of salt/hash - skip them
        if rec.get("revoked") or not rec.get("salt") or not rec.get("hash"):
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



# ── device memory (remember mode) ──────────────────────────────────────────
def mode(doc: dict) -> str:
    """'remember' = a bound device skips the key; 'strict' = key every start."""
    return str(doc.get("mode") or "remember").lower()


def is_remember(doc: dict) -> bool:
    return mode(doc) != "strict"


def bound_for(fp: str, doc: dict) -> dict | None:
    """The active key already welded to this device, if any."""
    for rec in doc.get("keys", []):
        if rec.get("revoked") or not rec.get("bound_device"):
            continue
        if hmac.compare_digest(str(rec["bound_device"]), str(fp)):
            return rec
    return None


def touch(rec: dict) -> None:
    """Count a start on an already bound device (no key retyped)."""
    doc = load()
    for r in doc.get("keys", []):
        if r.get("label") == rec.get("label") and not r.get("revoked"):
            r["uses"] = int(r.get("uses") or 0) + 1
            r["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save(doc)



# ── signed codes from the Telegram access bot ──────────────────────────────
def _b36decode(text: str) -> int:
    return int(text, 36)


def verify_signed(code: str) -> tuple[bool, str, str, int]:
    """Check NAI-<tgid36>-<exp36>-<sig8>. Offline: HMAC, no network.

    Returns (ok, message, tgid, expires_at).
    """
    parts = (code or "").strip().split("-")
    if len(parts) != 4 or parts[0].upper() != "NAI":
        return False, "not a signed access code", "", 0
    tg36, exp36, sig = parts[1], parts[2], parts[3].upper()
    try:
        tg_id = _b36decode(tg36)
        exp = _b36decode(exp36)
    except ValueError:
        return False, "malformed access code", "", 0
    payload = f"{tg36}.{exp36}"
    want = hmac.new(SIGN_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:8].upper()
    if not hmac.compare_digest(want, sig):
        return False, "this access code is not valid (signature)", "", 0
    now = int(time.time())
    if exp < now:
        mins = int((now - exp) / 60)
        return False, f"this access code expired {mins} minute(s) ago - send /start again", "", exp
    return True, "ok", str(tg_id), exp


def _code_record(doc: dict, tg_id: str, code: str) -> dict:
    """Find or create the record that remembers which phone used this code."""
    fp_code = hashlib.sha256(code.strip().encode()).hexdigest()[:16]
    for rec in doc.get("keys", []):
        if rec.get("code_fp") == fp_code:
            return rec
    rec = {
        "label": f"tg:{tg_id}",
        "code_fp": fp_code,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bound_device": "",
        "bound_at": "",
        "last_used": "",
        "uses": 0,
        "revoked": False,
    }
    doc.setdefault("keys", []).append(rec)
    return rec


def redeem_signed(code: str, doc: dict) -> tuple[bool, str, dict | None]:
    """Verify a Telegram code and weld it to this device."""
    ok, msg, tg_id, _exp = verify_signed(code)
    if not ok:
        return False, msg, None
    rec = _code_record(doc, tg_id, code)
    if rec.get("revoked"):
        return False, "this access code was revoked by the owner", rec
    fp = device_fingerprint()
    if not rec.get("bound_device"):
        rec.update(bound_device=fp, uses=1,
                   bound_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        rec["last_used"] = rec["bound_at"]
        save(doc)
        return True, "code accepted and locked to this phone", rec
    if rec["bound_device"] != fp:
        return False, "this code was already used on another phone - get a new one", rec
    rec["uses"] = int(rec.get("uses") or 0) + 1
    rec["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save(doc)
    return True, "welcome back", rec



def _try_all(code: str) -> tuple[bool, str, dict | None]:
    """Accept either a Telegram signed code or an older NAI-... key."""
    ok, msg, rec = redeem_signed(code, load())
    if ok:
        return ok, msg, rec
    legacy_ok, legacy_msg, legacy_rec = redeem(code, load())
    if legacy_ok:
        return legacy_ok, legacy_msg, legacy_rec
    _, why, _tg, _exp = verify_signed(code)
    return False, (msg if why != "not a signed access code" else legacy_msg), None


def require_access(purpose: str = "this program") -> None:
    """Let the person in with a Telegram access code. Nothing else.

    The prompt shows which bot to open. They press Start there, the bot sends a
    signed code valid 30 minutes, they paste it here. The first phone to use a
    code keeps it; the same code on another phone is refused. Once this phone is
    activated it is remembered and stops asking (setmode strict to ask always).
    Older NAI-xxxx-xxxx-xxxx keys from access_keys.json still work.
    """
    doc = load()

    env_key = os.environ.get(TOKEN_ENV, "").strip()
    if env_key:
        ok, msg, rec = _try_all(env_key)
        if not ok:
            _fail(f"[access] {TOKEN_ENV} rejected: {msg}")
        print(f"[access] verified from environment ({rec.get('label')})")
        return

    if os.environ.get("GITHUB_ACTIONS") == "true" and not sys.stdin.isatty():
        print("[access] headless runner - skipping the interactive code check. "
              "Set BOT_ACCESS_KEY to require it there too.")
        return

    fp = device_fingerprint()
    if is_remember(doc):
        known = bound_for(fp, doc)
        if known:
            print(f"[access] this phone is activated ({known['label']}) - go ahead")
            touch(known)
            return

    print(f"\n=== {purpose} is locked ===")
    print(f"1) Telegram খুলে @{TG_BOT} খোঁজো, Start চাপো")
    print(f"2) যে কোডটা আসবে সেটা এখানে পেস্ট করো")
    for attempt in range(1, ATTEMPTS + 1):
        left = ATTEMPTS - attempt + 1
        try:
            code = input(f"Access code ({left} tries left): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[access] cancelled.")
            raise SystemExit(1)
        if not code:
            continue
        ok, msg, rec = _try_all(code)
        if ok:
            print(f"[access] {msg}")
            return
        print(f"[access] {msg}")
    _fail("[access] too many failed attempts - not starting.")


# ── CLI ────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "status").lower()
    if cmd == "setmode":
        want = (argv[2] if len(argv) > 2 else "").lower()
        if want not in ("remember", "strict"):
            print("usage: python3 access.py setmode remember|strict")
            print("  remember = a phone that already redeemed a key skips the key")
            print("  strict   = the key is required on every single start")
            return 2
        doc = load(); doc["mode"] = want; save(doc)
        print(f"mode set to {want}")
        return 0

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
    print(f"  mode         : {mode(doc)}"
          + ("  (a bound phone skips the key)" if is_remember(doc)
             else "  (key required on every start)"))
    print(f"  this device  : {device_fingerprint()[:16]}...")
    print(f"  key file     : {os.path.basename(KEYS_FILE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
