#!/usr/bin/env python3
"""SELFTEST — the guard that was missing.

Twice today a change shipped that broke a working tool: a NameError inside
main() (full_login used before import) and a diagnostic script that was shown in
chat but never committed. Both were findable in seconds without touching the
network. This file is the check to run BEFORE every push:

    python3 selftest.py

It imports every tool, exercises the mint chain with the network mocked out,
verifies the device fingerprint stays stable for one account, and confirms each
script's CLI still parses. Exits non-zero on the first failure.
"""
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "guests_manager"))

FAILS = []
CHECKS = 0


def check(name):
    def deco(fn):
        global CHECKS
        CHECKS += 1
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
            FAILS.append((name, traceback.format_exc()))
        return fn
    return deco


print("imports")
import full_login          # noqa: E402
import guestgen            # noqa: E402
import ff_bd_api as api    # noqa: E402


@check("guestgen exposes full_login at module level (NameError guard)")
def _():
    assert guestgen.full_login is full_login


@check("guestgen.LEGACY_LOGIN and make_nick exist")
def _():
    assert hasattr(guestgen, "LEGACY_LOGIN")
    n = guestgen.make_nick()
    assert n.startswith("SB\u3164"), repr(n)


@check("device fingerprint is stable and persisted per account")
def _():
    a = full_login.device_for("selftest-uid")
    b = full_login.device_for("selftest-uid")
    assert a["unique_device_id"] == b["unique_device_id"]
    table = json.loads((ROOT / "guests_manager" / "devices.json").read_text())
    assert "selftest-uid" in table
    table.pop("selftest-uid", None)          # leave no test rows behind
    (ROOT / "guests_manager" / "devices.json").write_text(json.dumps(table, indent=1))


@check("login payload honours the requested region and sends a real client_ip")
def _():
    full_login.PUBLIC_IP = "203.0.113.9"
    bd = full_login.build_full_payload("oid", "tok", {}, region="BD")
    idn = full_login.build_full_payload("oid", "tok", {}, region="ID")
    assert bd != idn, "region did not reach the payload"
    assert full_login.public_ip() == "203.0.113.9"


@check("ChooseRegion body is a valid AES block")
def _():
    from guests_manager import game_register as gr
    body = gr.encrypt_payload(gr.pf(1, "BD"))
    assert len(body) == 16, len(body)


@check("mint chain completes with the network mocked")
def _():
    from guests_manager import game_register as gr
    saved = (gr.msdk_register, gr.token_grant, gr.major_register, full_login.full_major_login)
    try:
        gr.msdk_register = lambda sess, timeout=15: (("1234567890", "pw"), "ok")
        gr.token_grant = lambda sess, uid, pw: ("at", "oid")
        gr.major_register = lambda sess, at, oid, nick: (True, "ok")
        full_login.full_major_login = lambda uid, oid, at, **kw: {
            "jwt": "J" * 819, "key": b"\x01" * 32, "iv": b"\x02" * 16,
            "server_url": "https://clientbp.ppmainecoonghj.com",
            "url": "https://clientbp.ppmainecoonghj.com", "account_id": 1234567890}
        rec = guestgen.mint_one(None, {"release": "OB55",
                                       "login_host": "loginbp.ppmainecoonghj.com",
                                       "region": "BD"})
    finally:
        (gr.msdk_register, gr.token_grant, gr.major_register,
         full_login.full_major_login) = saved
    assert rec, "mint_one returned None"
    for k in ("uid", "pass", "token", "server_url", "region", "key", "iv"):
        assert rec.get(k), f"missing {k}"
    assert rec["region"] == "BD"
    assert len(rec["key"]) == 64, "key must be hex, not raw bytes"
    json.dumps(rec)                                   # must be serialisable


@check("login_locked runs ChooseRegion when the server gave no game host")
def _():
    calls = []
    saved = (full_login.full_major_login, full_login.choose_region, full_login.time.sleep)
    try:
        full_login.full_major_login = lambda uid, oid, at, **kw: (
            calls.append("login") or {"jwt": "J" * 819, "key": b"\x01" * 32,
                                      "iv": b"\x02" * 16, "url": "https://loginbp.ppmainecoonghj.com"})
        full_login.choose_region = lambda *a, **k: (calls.append("choose") or 200)
        full_login.time.sleep = lambda s: None
        full_login.login_locked("1", "o", "a", region="BD")
    finally:
        (full_login.full_major_login, full_login.choose_region,
         full_login.time.sleep) = saved
    assert calls == ["login", "choose", "login"], calls


@check("like payload matches the documented proto (uid=1, region=2)")
def _():
    import like_pb2 as lpb
    fields = [(f.name, f.number) for f in lpb.like.DESCRIPTOR.fields]
    assert fields == [("uid", 1), ("region", 2)], fields


print("command line interfaces")
for script in ("guestgen.py", "whois.py", "like_canary.py", "like_shape.py",
               "like_variants.py", "supply.py"):
    CHECKS += 1
    try:
        r = subprocess.run([sys.executable, str(ROOT / script), "--help"],
                           capture_output=True, text=True, timeout=90, cwd=ROOT)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1][:120])
        print(f"  ok   {script} --help")
    except Exception as exc:
        print(f"  FAIL {script} --help: {exc}")
        FAILS.append((script, str(exc)))

print()
if FAILS:
    print(f"{len(FAILS)} of {CHECKS} checks failed")
    for name, tb in FAILS:
        print(f"\n--- {name}\n{tb}")
    sys.exit(1)
print(f"all {CHECKS} checks passed - safe to push")
