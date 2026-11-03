#!/usr/bin/env python3
"""Mint like-pool tokens from a BD connection (run this on the phone).

    python3 mint_pool_phone.py                # 6 tokens from accounts already on file
    python3 mint_pool_phone.py --register 6   # register 6 BRAND-NEW accounts and mint them

Garena refuses MajorLogin for accounts older than a few days ("Protection Bypass"),
so once the fresh ones are spent the only way forward is registering new guests and
logging them in immediately - which is what --register does.

Why this exists: Garena stamps a guest account's region from the IP that logs it
in, so tokens for the like pool can only be minted from Bangladesh. The GitHub
runner is not in BD and cannot do it. Your phone is, so this script mints a few
tokens there, appends them to bd_pool.json and pushes, and the 24/7 bot on GitHub
uses them. Tokens live about 7 hours, so running this twice a day keeps /like and
/profile working with nothing else to do.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import ff_bd_api as api
import random
import secrets
import string
import sys

sys.path.insert(0, str(Path(__file__).parent))
from guests_manager import game_register as gr  # noqa: E402

ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
CHECK = "--check" in sys.argv
DO_REGISTER = "--register" in sys.argv
N = int(ARGS[0]) if ARGS else 6
POOL = Path(__file__).parent / "bd_pool.json"



def register_and_mint(n):
    """Register n fresh guests and log each one in straight away (BD IP required)."""
    made = []
    sess = gr.new_session()
    for i in range(n):
        print(f"[{i+1}/{n}] registering guest ... ", end="", flush=True)
        cred, why = gr.msdk_register(sess)
        if not cred:
            print(f"register failed ({why}) - stopping")
            break
        uid, pw = cred
        at, oid = gr.token_grant(sess, uid, pw)
        if not at:
            print("grant failed - stopping")
            break
        nick = "ANON\u2022" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
        ok, msg = gr.major_register(sess, at, oid, nick)
        if not ok:
            print(f"major register failed ({msg}) - stopping")
            break
        print(f"{nick} (uid {uid})", end=" ")
        tok, reason = api.mint_jwt_detailed(str(uid), pw)
        if not tok:
            print(f"login failed: {reason}")
            api._burned_add([str(uid)])
            time.sleep(6)
            continue
        tok["name"] = nick
        tok["level"] = None
        made.append(tok)
        print("-> token ok")
        time.sleep(6)               # registration throttles quickly from one IP
    return made


def push(msg):
    cmds = [
        ["git", "add", "bd_pool.json", "like_burned.json"],
        ["git", "-c", "user.name=phone-mint", "-c", "user.email=phone@nextai.local",
         "commit", "-m", msg],
        ["git", "pull", "--rebase", "--autostash"],
        ["git", "push"],
    ]
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            print(f"[push] {c[1]} -> {r.stderr.strip()[:200]}")


def main():
    if CHECK:
        print("connection exits in:", (api._exit_country() or "unknown").upper())
        print("endpoint reachability from this phone:")
        for host, status in api.check_endpoints():
            print(f"  {host:45s} {status}")
        print("\nIf a host says TCP 443 failed, that network blocks it; the mint now "
              "tries the other grant host automatically.")
        return 0
    cc = (api._exit_country() or "").upper()
    print(f"connection exits in: {cc or 'unknown'}")
    if cc != "BD":
        print("Not a BD connection, so no account is being spent. "
              "Switch the phone to mobile data (or a BD wifi) and run again.")
        return 1

    pool = []
    try:
        pool = json.loads(POOL.read_text())
    except Exception:
        pass
    now = time.time()
    pool = [t for t in pool if (t.get("exp") or 0) > now + 300]
    have = {str(t.get("uid")) for t in pool}
    burned = api._burned_load()

    cands = []
    for uid, pw in api.load_creds() + api.load_registered_bd():
        u = str(uid)
        if u in burned or u in have:
            continue
        cands.append((int(u) if u.isdigit() else 0, u, pw))
    cands.sort(reverse=True)          # newest registrations first
    print(f"candidates available: {len(cands)} | already in pool: {len(pool)}")

    ok, tried, consec = 0, [], 0
    for _n, uid, pw in cands:
        if ok >= N:
            break
        if ok:
            time.sleep(5)             # gentle: Garena throttles a burst from one IP
        print(f"minting {uid} ... ", end="", flush=True)
        tried.append(uid)
        try:
            t, reason = api.mint_jwt_detailed(uid, pw)
        except Exception as ex:
            t, reason = None, f"error {ex}"
        if not t:
            print(f"failed - {reason}")
            consec += 1
            if consec >= 3 and "Protection Bypass" not in reason and "no jwt" not in reason:
                print("three failures in a row from this network - stopping before more accounts are touched")
                break
            continue
        consec = 0
        url = str(t.get("server_url") or "")
        if url and not api._hosts.is_bd_host(url):
            print(f"wrong region ({url}) - stopping")
            break
        pool.append(t)
        ok += 1
        print("ok")

    if DO_REGISTER:
        for t in register_and_mint(N):
            pool.append(t)
            ok += 1
    api._burned_add(tried)            # a login is spent either way
    POOL.write_text(json.dumps(pool, indent=1))
    print(f"pool now holds {len(pool)} live tokens ({ok} new)")
    if ok:
        push(f"phone mint: {ok} fresh BD like tokens")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
