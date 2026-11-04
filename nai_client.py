#!/usr/bin/env python3
"""NAI installation identity + activation client.

Every fresh installation gets a random Installation ID kept OUTSIDE the git
repository (~/.nai/installation.json). The server, not this file, decides
whether that installation may run:

    python3 nai_client.py status      # what the server says right now
    python3 nai_client.py activate NAI-XXXX-XXXX-XXXX

Nothing here is trusted by the server. Deleting the local files keeps the
server-side activation intact, so a re-clone that loses ~/.nai is a NEW
installation and needs a new code. That is the intended behaviour.
"""
from __future__ import annotations

import json
import os
import platform
import secrets
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("NAI_API", "https://solas-5609a435.base44.app/functions/naiAccess")
HOME = os.environ.get("NAI_HOME", os.path.expanduser(os.path.join("~", ".nai")))
INSTALL_FILE = os.path.join(HOME, "installation.json")
STATE_FILE = os.path.join(HOME, "activation.json")
OFFLINE_GRACE_HOURS = 24
APP = "ff-like-bot"


# ── files ──────────────────────────────────────────────────────────────────
def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _write(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def exists() -> bool:
    """True when this installation already has an identity."""
    return bool(_read(INSTALL_FILE).get("installation_id"))


def ensure_install() -> str:
    """Return this installation's id, creating it on first run."""
    doc = _read(INSTALL_FILE)
    iid = str(doc.get("installation_id") or "")
    if len(iid) < 16:
        iid = secrets.token_hex(16)
        _write(INSTALL_FILE, {
            "installation_id": iid,
            "app": APP,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": platform.node(),
        })
    return iid


def installation_id() -> str:
    return ensure_install()


def state() -> dict:
    return _read(STATE_FILE)


def _save_state(**patch) -> None:
    doc = state()
    doc.update(patch)
    doc["installation_id"] = installation_id()
    _write(STATE_FILE, doc)


# ── transport ──────────────────────────────────────────────────────────────
def _post(payload: dict, timeout: int = 15) -> tuple[dict, str]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(API, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read() or b"{}"), ""
    except urllib.error.HTTPError as ex:
        try:
            return json.loads(ex.read() or b"{}"), ""
        except Exception:
            return {}, f"server said HTTP {ex.code}"
    except Exception as ex:
        return {}, f"no contact with the access server ({str(ex)[:60]})"


# ── operations ─────────────────────────────────────────────────────────────
def activate(code: str) -> tuple[bool, str]:
    code = (code or "").strip().upper()
    if not code:
        return False, "empty code"
    out, why = _post({
        "action": "activate",
        "code": code,
        "installation_id": installation_id(),
        "app_version": APP,
    }, timeout=25)
    if why:
        return False, why
    if not out.get("ok"):
        return False, str(out.get("message") or out.get("status") or "refused")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    act = out.get("activation") or {}
    _save_state(status="ACTIVE", activated_at=act.get("activated_at") or now,
                activation_id=act.get("activation_id") or "", last_verified=now)
    return True, "installation activated"


def check() -> tuple[str, str]:
    """Ask the server. Returns ACTIVE / REVOKED / EXPIRED / NOT_ACTIVATED / OFFLINE."""
    out, why = _post({"action": "verify", "installation_id": installation_id()})
    if why:
        return "OFFLINE", why
    st = str(out.get("status") or "")
    if out.get("ok") and st == "ACTIVE":
        _save_state(status="ACTIVE", last_verified=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return "ACTIVE", "allowed"
    if st in ("REVOKED", "EXPIRED"):
        _save_state(status=st)
        return st, str(out.get("message") or st.lower())
    if st == "NOT_ACTIVATED":
        return "NOT_ACTIVATED", str(out.get("message") or "not activated")
    return "OFFLINE", str(out.get("message") or "unexpected answer")


def offline_grace() -> tuple[bool, str]:
    """No internet: allow a short window since the last good check."""
    doc = state()
    last = str(doc.get("last_verified") or "")
    if not last or doc.get("status") != "ACTIVE":
        return False, "no verified activation on record"
    try:
        age_h = (time.time() - time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%SZ"))) / 3600
    except Exception:
        return False, "unreadable last check time"
    if age_h <= OFFLINE_GRACE_HOURS:
        return True, f"{age_h:.1f}h since the last good check"
    return False, f"{age_h:.1f}h without a server check (limit {OFFLINE_GRACE_HOURS}h)"


def describe() -> str:
    iid = installation_id()
    st, why = check()
    doc = state()
    lines = [
        f"Installation : ...{iid[-6:]}" if iid else "Installation : (none)",
        f"Server state : {st}",
        f"Detail       : {why}",
        f"Activated at : {doc.get('activated_at') or '-'}",
        f"Last verified: {doc.get('last_verified') or '-'}",
        f"Local files  : {HOME}",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "status").lower()
    if cmd == "status":
        print(describe())
        return 0
    if cmd == "activate":
        if len(argv) < 3:
            print("usage: nai_client.py activate NAI-XXXX-XXXX-XXXX")
            return 2
        ok, msg = activate(argv[2])
        print(("✓ " if ok else "✗ ") + msg)
        return 0 if ok else 1
    if cmd == "id":
        print(installation_id())
        return 0
    print("commands: status | activate <code> | id")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
