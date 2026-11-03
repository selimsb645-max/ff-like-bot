#!/usr/bin/env python3
"""Mint like-pool tokens from a BD connection (run this on the phone).

    python3 mint_pool_phone.py 6

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

N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
POOL = Path(__file__).parent / "bd_pool.json"


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

    ok, tried = 0, []
    for _n, uid, pw in cands:
        if ok >= N:
            break
        if ok:
            time.sleep(5)             # gentle: Garena throttles a burst from one IP
        print(f"minting {uid} ... ", end="", flush=True)
        tried.append(uid)
        try:
            t = api.mint_jwt(uid, pw)
        except Exception as ex:
            t = None
            print(f"error {ex}")
        if not t:
            print("failed")
            continue
        url = str(t.get("server_url") or "")
        if url and not api._hosts.is_bd_host(url):
            print(f"wrong region ({url}) - stopping")
            break
        pool.append(t)
        ok += 1
        print("ok")

    api._burned_add(tried)            # a login is spent either way
    POOL.write_text(json.dumps(pool, indent=1))
    print(f"pool now holds {len(pool)} live tokens ({ok} new)")
    if ok:
        push(f"phone mint: {ok} fresh BD like tokens")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
