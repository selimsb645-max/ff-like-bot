#!/usr/bin/env python3
"""Mint guest accounts WITHOUT logging them in.

Why this exists: create_four_accounts() finishes by calling MajorLogin, and a
guest account can only be logged in once. So the play engine (main.py) could
never log in afterwards. This script stops right after MajorRegister, leaving
the account untouched so main.py gets the single login.

    python3 mint_accounts.py 4          # mint 4 accounts into accounts.json
    python3 mint_accounts.py 4 --gap 8  # 8s pause between registrations

It prints a summary and appends to accounts.json (uid/password/region).
"""
import argparse
import asyncio
import json
import os
import random
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as bot

ACCOUNTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json")


async def mint_one(region, rel, rv, login_url, gop1, appv):
    uid = None
    for attempt in range(3):
        uid = await bot.register_account("ANON", gop1, appv)
        if uid:
            break
        print(f"    register attempt {attempt + 1} failed, waiting 6s")
        await asyncio.sleep(6)
    if not uid:
        return None
    tok = await bot.grant_token(uid, "ANON", gop1, appv)
    if not tok:
        print("    grant failed")
        return None
    nick = "ANON•" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    mr = await bot.major_register(rel, tok["access_token"], tok["open_id"], nick,
                                  rv, login_url, "bn")
    acc_id = None
    if isinstance(mr, dict):
        v = mr.get("3", mr.get(3))
        acc_id = v.get("data") if isinstance(v, dict) else v
    if not acc_id:
        print("    MajorRegister failed (check the ANON•XXXXX nickname format)")
        return None
    print(f"    minted {nick} | auth {uid} | game id {acc_id}")
    return {"uid": str(uid), "password": "ANON", "region": region, "account_id": str(acc_id)}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("count", nargs="?", type=int, default=4)
    ap.add_argument("--region", default="BD")
    ap.add_argument("--gap", type=float, default=7.0, help="seconds between registrations")
    a = ap.parse_args()

    vc = await bot.version_config(a.region)
    if not vc:
        print("version config failed")
        return
    rel, rv, login_url, gop1, appv = vc
    print(f"version {rel} / {rv} / {login_url}")

    made = []
    for i in range(a.count):
        print(f"[{i + 1}/{a.count}] minting...")
        acc = await mint_one(a.region, rel, rv, login_url, gop1, appv)
        if acc:
            made.append(acc)
        if i < a.count - 1:
            await asyncio.sleep(a.gap)          # registration throttles in bursts

    if not made:
        print("nothing minted")
        return
    existing = []
    if os.path.exists(ACCOUNTS):
        try:
            with open(ACCOUNTS, "r", encoding="utf-8") as f:
                existing = json.load(f) or []
        except Exception:
            existing = []
    have = {str(x.get("uid")) for x in existing if isinstance(x, dict)}
    existing.extend([m for m in made if m["uid"] not in have])
    with open(ACCOUNTS, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    print(f"\nminted {len(made)} account(s), accounts.json now has {len(existing)}")
    print("next: python3 main.py   (it performs the single login and plays)")


if __name__ == "__main__":
    asyncio.run(main())
