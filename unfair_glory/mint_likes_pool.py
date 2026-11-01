#!/usr/bin/env python3
"""Register fresh BD guest accounts and add them to the LIKE pool.

Registration only - never logs in. A guest account can be logged in exactly once,
so leaving the login for the like bot is what keeps each account usable.

    python3 unfair_glory/mint_likes_pool.py --count 300

Writes to <repo>/phone_accounts.json (the file ff_bd_api.load_creds() merges), as
{"uid", "pw", "region", "nick", "account_id", "source": "auto_mint", "ts"}.

Garena throttles guest registration per IP, so the run paces itself, stops early
after a streak of failures, and records progress in .mint_likes_state.json so the
next run continues instead of starting over.
"""
import argparse
import asyncio
import json
import os
import random
import string
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import app as bot                                    # noqa: E402  (UNFAIR pipeline)

POOL_OUT = os.path.join(ROOT, "phone_accounts.json")
STATE = os.path.join(HERE, ".mint_likes_state.json")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_pool(entries):
    tmp = POOL_OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=1)
    os.replace(tmp, POOL_OUT)


def known_uids(entries):
    return {str(e.get("uid")) for e in entries if isinstance(e, dict)}


async def _probe_region(uid, rel, rv, login_url, gop1, appv, want="BD"):
    """Read which region this IP just registered the account in.

    Garena fixes an account's region at registration from the caller's IP. GitHub
    Actions and datacenter IPs register US/other accounts, and a US token can only
    reach client.us.freefiremobile.com - asking it for a BD uid answers
    BR_ACCOUNT_NOT_FOUND, so those accounts are useless for BD likes and must not
    be added to the pool.
    """
    try:
        tok = await bot.grant_token(uid, "ANON", gop1, appv)
        if not tok:
            return None
        mp, _ = await bot.get_payload(tok["access_token"], tok["open_id"],
                                      tok.get("platform", 4), rv, "bn")
        res = await bot.major_login(mp, login_url, rel)
        if not isinstance(res, dict):
            return None
        v = res.get("10", res.get(10))
        srv = v.get("data") if isinstance(v, dict) and "data" in v else v
        return srv
    except Exception:
        return None

async def mint_one(rel, rv, login_url, gop1, appv):
    uid = None
    for attempt in range(3):
        uid = await bot.register_account("ANON", gop1, appv)
        if uid:
            break
        await asyncio.sleep(10 + attempt * 10)       # throttle back off
    if not uid:
        return None, "register"
    tok = await bot.grant_token(uid, "ANON", gop1, appv)
    if not tok:
        return None, "grant"
    nick = "ANON•" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    mr = await bot.major_register(rel, tok["access_token"], tok["open_id"], nick,
                                  rv, login_url, "bn")
    acc = mr.get("3", mr.get(3)) if isinstance(mr, dict) else None
    acc_id = acc.get("data") if isinstance(acc, dict) else acc
    if not acc_id:
        return None, "major_register"
    return {"uid": str(uid), "pw": "ANON", "region": "BD", "nick": nick,
            "account_id": str(acc_id), "source": "auto_mint", "ts": int(time.time() * 1000)}, None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=300, help="accounts to add TODAY")
    ap.add_argument("--gap", type=float, default=6.0, help="seconds between registrations")
    ap.add_argument("--max-fail-streak", type=int, default=12)
    ap.add_argument("--region", default="BD")
    ap.add_argument("--skip-region-check", action="store_true")
    ap.add_argument("--time-budget", type=int, default=4 * 3600,
                    help="stop after this many seconds (GitHub job safety)")
    a = ap.parse_args()

    pool = load_json(POOL_OUT, [])
    if not isinstance(pool, list):
        pool = []
    state = load_json(STATE, {})
    today = time.strftime("%Y-%m-%d")
    if state.get("day") != today:
        state = {"day": today, "added": 0}
    already_today = int(state.get("added", 0))
    need = max(0, a.count - already_today)
    print(f"pool={len(pool)} accounts | today added={already_today} | target={a.count} | will mint={need}")
    if need == 0:
        print("daily target already met")
        return

    vc = await bot.version_config(a.region)
    if not vc:
        print("version config failed - aborting")
        return
    rel, rv, login_url, gop1, appv = vc
    print(f"version {rel}/{rv} via {login_url} | gap={a.gap}s")

    have = known_uids(pool)

    # Fail fast on a wrong region instead of filling the pool with unusable
    # accounts: probe the FIRST registration, and abort the whole run if this IP
    # is not creating BD accounts.
    if not a.skip_region_check:
        first, err = await mint_one(rel, rv, login_url, gop1, appv)
        if first:
            srv = await _probe_region(first["uid"], rel, rv, login_url, gop1, appv)
            print(f"region probe: server_url = {srv}")
            if srv and "clientbp" not in str(srv):
                print("ABORT: this IP registers non-BD accounts "
                      f"({srv}). They cannot like BD uids. Run this on the phone "
                      "(BD IP) or pass --skip-region-check to override.")
                return
            first["region"] = "BD"
            if first["uid"] not in have:
                pool.append(first); have.add(first["uid"])
                state["added"] = already_today + 1
                save_pool(pool)
                print(f"[1] ✅ {first['nick']} uid={first['uid']} (BD confirmed)")
        else:
            print(f"first mint failed ({err}) - stopping before wasting more attempts")
            return

    started = time.time()
    streak = 0
    added = 0
    for i in range(need):
        if time.time() - started > a.time_budget:
            print("time budget reached - stopping cleanly")
            break
        acc, err = await mint_one(rel, rv, login_url, gop1, appv)
        if acc:
            if acc["uid"] not in have:
                pool.append(acc)
                have.add(acc["uid"])
                added += 1
                state["added"] = already_today + added
                save_pool(pool)
                save_pool_done = True
                print(f"[{added}/{need}] ✅ {acc['nick']} uid={acc['uid']} (pool {len(pool)})")
            streak = 0
        else:
            streak += 1
            print(f"[{i+1}/{need}] ✗ {err} (fail streak {streak})")
            if streak >= a.max_fail_streak:
                print(f"stopping: {streak} failures in a row - IP is throttling registration")
                break
        await asyncio.sleep(a.gap + random.uniform(0, 2))

    state["day"] = today
    state["added"] = already_today + added
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    print(f"done: +{added} accounts | pool now {len(pool)} | today {state['added']}/{a.count}")


if __name__ == "__main__":
    asyncio.run(main())
