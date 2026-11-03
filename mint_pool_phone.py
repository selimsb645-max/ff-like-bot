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
import os
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
PUSH_ONLY = "--push" in sys.argv
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


SLUG = "selimsb645-max/ff-like-bot"


def owner_token():
    """The repo's existing owner-token convention: env first, then ~/.ff_owner_token."""
    for env in ("GITHUB_USAGE_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(env, "").strip()
        if v:
            return v
    f = Path.home() / ".ff_owner_token"
    try:
        return f.read_text().strip()
    except Exception:
        return ""


def token_status(tok):
    """Is this token accepted by GitHub for this repo? Checked before pushing so a
    bad copy-paste is reported as such instead of surfacing as a 403 mid-push."""
    import urllib.request
    req = urllib.request.Request(
        f"https://api.github.com/repos/{SLUG}",
        headers={"Authorization": f"Bearer {tok}", "User-Agent": "ff-phone"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status == 200, "ok"
    except Exception as ex:
        return False, f"{type(ex).__name__}: {getattr(ex, 'code', ex)}"


def push(msg):
    tok = owner_token()
    if not tok:
        print("[push] no token found. Save a fine-grained PAT with Contents: Read and")
        print("       write on this repo once:")
        print("         echo 'github_pat_xxx' > ~/.ff_owner_token && chmod 600 ~/.ff_owner_token")
        print("       then run: python3 mint_pool_phone.py --push")
        return False
    ok, why = token_status(tok)
    if not ok:
        print(f"[push] the saved token is not accepted by GitHub ({why}).")
        print("       Re-save it exactly as GitHub shows it, one line, no extra prefix:")
        print("         echo 'github_pat_...' > ~/.ff_owner_token && chmod 600 ~/.ff_owner_token")
        return False
    print(f"[push] token ok ({tok[:12]}...)")
    url = f"https://x-access-token:{tok}@github.com/{SLUG}.git"
    steps = [
        ["git", "add", "bd_pool.json", "like_burned.json"],
        ["git", "-c", "user.name=phone-mint", "-c", "user.email=phone@nextai.local",
         "commit", "-m", msg],
        ["git", "-c", "pull.rebase=true", "pull", "--rebase", "--autostash", url, "main"],
        ["git", "push", url, "HEAD:main"],
    ]
    for c in steps:
        r = subprocess.run(c, capture_output=True, text=True)
        out = (r.stdout + r.stderr)
        if r.returncode != 0:
            if "nothing to commit" in out:
                continue
            print(f"[push] {c[1] if len(c) > 1 else c[0]} failed: {out.strip()[:220]}")
            return False
    print("[push] bd_pool.json is on GitHub - the 24/7 bot can use these tokens now")
    return True


def main():
    if PUSH_ONLY:
        try:
            n = len([t for t in json.loads(POOL.read_text()) if (t.get("exp") or 0) > time.time()])
        except Exception:
            n = 0
        print(f"local pool holds {n} live tokens")
        return 0 if push("phone mint: pool tokens") else 3
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
