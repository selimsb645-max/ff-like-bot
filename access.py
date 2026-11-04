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
import urllib.error
import urllib.request

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
REDEEM_URL = "https://solas-5609a435.base44.app/functions/ffAccessRedeem"
OFFLINE_GRACE_HOURS = 24   # how long an activated phone may start with no internet
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
            parent = os.path.dirname(DEVICE_FILE)
            if parent:
                os.makedirs(parent, exist_ok=True)
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


# ── terminal look: colour when someone is watching, plain when piped ────────
def _use_colour() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


COLOUR = _use_colour()

GRAY, BOLD, CYAN, YELLOW, GREEN, RED = "90", "1", "96", "93", "92", "91"


def C(text: str, code: str = "") -> str:
    """Wrap text in an ANSI colour when colours are on."""
    return f"\033[{code}m{text}\033[0m" if (COLOUR and code) else text


def _english(msg: str) -> str:
    """The redeem endpoint answers bilingually; a terminal line wants one tongue."""
    return msg.split(" / ")[-1].strip() if " / " in msg else msg


def _rule(times: int = 58) -> str:
    return C("\u2500" * times, GRAY)


def _show_gate(purpose: str) -> None:
    print()
    print(_rule())
    print("  " + C("\U0001F512", BOLD) + "  " + C(f"{purpose} is locked", CYAN + ";1"))
    print(_rule())
    print("  " + C("1", BOLD) + "  Open Telegram, search " + C("@" + TG_BOT, YELLOW) + " and press "
          + C("START", YELLOW))
    print("  " + C("2", BOLD) + "  Paste the code it sends you below")
    print(_rule())
    print()


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




# ── server claim: device + public IP binding ───────────────────────────────
def redeem_remote(code: str) -> tuple[bool, str, dict | None]:
    """Ask the access server to weld this code to this phone and network.

    The code itself proves nothing on its own: the server checks it exists, is
    unexpired, unclaimed, and records the device fingerprint plus the public IP
    the request came from. A second phone gets a hard "already used".
    """
    code = (code or "").strip().upper()
    if not code:
        return False, "empty code", None
    payload = json.dumps({"code": code, "device": device_fingerprint()}).encode()
    req = urllib.request.Request(REDEEM_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as res:
            data = json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as ex:
        try:
            data = json.loads(ex.read() or b"{}")
        except Exception:
            data = {"ok": False, "message": f"server said HTTP {ex.code}"}
    except Exception as ex:
        return False, f"could not reach the access server ({str(ex)[:60]})", None

    msg = data.get("message") or data.get("error") or "refused"
    if not data.get("ok"):
        return False, msg, None

    doc = load()
    fp_code = hashlib.sha256(code.encode()).hexdigest()[:16]
    rec = next((r for r in doc.get("keys", []) if r.get("code_fp") == fp_code), None)
    if not rec:
        rec = {"label": f"code:{code[-4:]}", "code_fp": fp_code, "revoked": False}
        doc.setdefault("keys", []).append(rec)
    rec["bound_device"] = device_fingerprint()
    rec["bound_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rec["last_used"] = rec["bound_at"]
    rec["uses"] = int(rec.get("uses") or 0) + 1
    rec["ip"] = data.get("ip", "")
    save(doc)
    return True, msg, rec


def _try_all(code: str) -> tuple[bool, str, dict | None]:
    """Accept a server-issued code, a signed code, or an older NAI-... key."""
    ok, msg, rec = redeem_remote(code)
    if ok:
        return ok, msg, rec
    server_msg = msg
    ok, msg, rec = redeem_signed(code, load())
    if ok:
        return ok, msg, rec
    legacy_ok, legacy_msg, legacy_rec = redeem(code, load())
    if legacy_ok:
        return legacy_ok, legacy_msg, legacy_rec
    _, why, _tg, _exp = verify_signed(code)
    if why != "not a signed access code":
        return False, msg, None
    legit = code.strip().upper().startswith("NAI-")
    return False, (legacy_msg if not legit else server_msg), None



def check_remote(rec: dict) -> tuple[str, str]:
    """Ask the server whether this phone is still allowed to start.

    Returns (state, message) where state is "ok", "revoked", "unknown" or
    "offline". Called on every start so revoking a code in Telegram takes effect
    on that person's next launch, without anything changing on their phone.
    """
    payload = json.dumps({"action": "check", "device": device_fingerprint()}).encode()
    req = urllib.request.Request(REDEEM_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as res:
            data = json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as ex:
        try:
            data = json.loads(ex.read() or b"{}")
        except Exception:
            return "offline", f"server said HTTP {ex.code}"
    except Exception:
        return "offline", "no internet"

    status = data.get("status")
    if status == "revoked":
        return "revoked", data.get("message", "access revoked")
    if data.get("ok"):
        return "ok", data.get("message", "allowed")
    if status == "unknown":
        return "unknown", data.get("message", "this phone is not on the server")
    return "offline", data.get("message", "unexpected answer")


def _stamp_verified(rec: dict) -> None:
    doc = load()
    for r in doc.get("keys", []):
        if r.get("label") == rec.get("label"):
            r["last_verified"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save(doc)


def _mark_local_revoked(rec: dict) -> None:
    doc = load()
    for r in doc.get("keys", []):
        if r.get("label") == rec.get("label"):
            r["revoked"] = True
    save(doc)


def require_access(purpose: str = "this program") -> None:
    """Let the person in with a Telegram access code. Nothing else.

    The prompt shows which bot to open. They press Start there, the bot sends a
    signed code valid 5 minutes, they paste it here. The first phone to use a
    code keeps it; the same code on another phone is refused. Once this phone is
    activated it is remembered and stops asking (setmode strict to ask always).
    Older NAI-xxxx-xxxx-xxxx keys from access_keys.json still work.
    """
    doc = load()

    if os.environ.get("FF_SKIP_ACCESS", "").strip() in ("1", "true", "yes"):
        # Owner's own device: he asked for the gate not to stand between him and the bot.
        print("  " + C("access gate skipped (FF_SKIP_ACCESS)", GRAY))
        return

    env_key = os.environ.get(TOKEN_ENV, "").strip()
    if env_key:
        ok, msg, rec = _try_all(env_key)
        if not ok:
            _fail(f"[access] {TOKEN_ENV} rejected: {msg}")
        print("  " + C(f"\u2713 activated from environment ({rec.get('label')})", GREEN))
        return

    if os.environ.get("GITHUB_ACTIONS") == "true" and not sys.stdin.isatty():
        print("[access] headless runner - skipping the interactive code check. "
              "Set BOT_ACCESS_KEY to require it there too.")
        return

    fp = device_fingerprint()
    if is_remember(doc):
        known = bound_for(fp, doc)
        if known:
            state, why = check_remote(known)
            if state == "ok":
                _stamp_verified(known)
                touch(known)
                print("  " + C(f"\u2713 activated ({known['label']})", GREEN) + C(" - go ahead", GRAY))
                return
            if state == "revoked":
                _mark_local_revoked(known)
                _fail("  " + C(f"access to this phone was revoked ({_english(why)})", RED + ";1")
                      + "\n  " + C(f"get a new code from @{TG_BOT}", GRAY))
            # server unreachable or the record is gone: allow a short grace period
            last = str(known.get("last_verified") or known.get("bound_at") or "")
            if last:
                try:
                    age_h = (time.time() - time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%SZ"))) / 3600
                except Exception:
                    age_h = 999.0
                if age_h <= OFFLINE_GRACE_HOURS:
                    print("  " + C(f"server not reachable ({_english(why)}); last check {age_h:.1f}h ago", YELLOW)
                          + " " + C(f"- allowing up to {OFFLINE_GRACE_HOURS}h offline", GRAY))
                    touch(known)
                    return
                _fail("  " + C(f"no contact with the server for {OFFLINE_GRACE_HOURS}h ({_english(why)})", RED + ";1")
                      + "\n  " + C("start once with internet, then try again", GRAY))
            print("  " + C(f"server not reachable ({_english(why)}) - asking for a code", YELLOW))

    if not sys.stdin.isatty():
        # A restart wrapper runs with no terminal, so the code prompt would read EOF and
        # report "cancelled" forever. Say what actually has to happen instead.
        _fail("  " + C("this phone is not activated yet, and there is no terminal to type the code into",
                       RED + ";1")
              + "\n  " + C(f"run it once in the foreground:  python3 bot.py", GRAY)
              + "\n  " + C(f"then paste the code from @{TG_BOT} and restart the background loop", GRAY))

    _show_gate(purpose)
    for attempt in range(1, ATTEMPTS + 1):
        left = ATTEMPTS - attempt + 1
        label = "try" if left == 1 else "tries"
        try:
            code = input("  " + C(f"Access code ({left} {label} left): ", YELLOW + ";1")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  " + C("cancelled", RED))
            raise SystemExit(1)
        if not code:
            continue
        ok, msg, rec = _try_all(code)
        if ok:
            print("  " + C("\u2713 " + _english(msg), GREEN + ";1"))
            print(_rule())
            print()
            return
        print("  " + C("\u2715 " + _english(msg), RED))
        print()
    _fail("  " + C("too many failed attempts - not starting", RED))


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
