#!/usr/bin/env python3
"""LIKE VARIANTS — which LikeProfile body does Garena actually credit?

Everything so far sent one shape: like{uid: <target uid>, region: "BD"}. The
server answers 200 with an empty body and never moves the counter, so either the
body is wrong or the liker is ineligible. This tries the four shapes that matter,
ONE AT A TIME, and reads the counter after each, so a rise can be attributed.

    python3 like_variants.py --uid 8409859081

Run on the phone (residential BD IP). Nothing is written to the pool.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "guests_manager")

import ff_bd_api as api   # noqa: E402
import like_pb2 as lpb    # noqa: E402


def read_likes(uid):
    try:
        d = api.get_profile(uid) or {}
        return d.get("likes")
    except Exception as exc:
        print(f"  read failed: {type(exc).__name__}: {exc}")
        return None


def payload(uid_value, region=None):
    m = lpb.like()
    m.uid = int(uid_value)
    if region:
        m.region = region
    return api._enc(m.SerializeToString())


def likers(n):
    raw = json.loads(Path("bd_pool.json").read_text())
    recs = raw if isinstance(raw, list) else list(raw.values())
    ok = [r for r in recs if isinstance(r, dict) and str(r.get("region", "BD")).upper() == "BD"
          and r.get("token") and (r.get("pass") or r.get("pw"))]
    ok.sort(key=lambda r: r.get("ts") or r.get("exp") or 0, reverse=True)
    return ok[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", required=True, help="target uid")
    ap.add_argument("--account-id", default="", help="target internal account id, if you know it")
    ap.add_argument("--wait", type=int, default=45, help="seconds to wait per variant")
    args = ap.parse_args()

    tgt_uid = int(args.uid)
    tgt_acc = int(args.account_id) if args.account_id else None

    prof = api.get_profile(tgt_uid) or {}
    if not tgt_acc:
        tgt_acc = prof.get("account_id")
    print(f"target uid : {tgt_uid}")
    print(f"target acc : {tgt_acc}  (from {'--account-id' if args.account_id else 'profile'})")
    print(f"target likes now: {prof.get('likes')}")

    variants = [
        ("uid + region BD", tgt_uid, "BD"),
        ("uid only", tgt_uid, None),
    ]
    if tgt_acc:
        variants += [
            ("account_id + region BD", tgt_acc, "BD"),
            ("account_id only", tgt_acc, None),
        ]

    pool = likers(len(variants) + 2)
    if len(pool) < len(variants):
        print(f"need {len(variants)} likers, pool has {len(pool)}")
        return 2

    results = []
    for i, (label, val, reg) in enumerate(variants):
        rec = pool[i]
        before = read_likes(tgt_uid)
        body = payload(val, reg)
        try:
            r = api._client().post(
                api._api_base({"token": rec["token"], "server_url": rec.get("server_url")}) + "/LikeProfile",
                headers=api._headers(rec["token"], "OB55"), data=body)
            status = f"{r.status_code} {len(r.content)}B"
            if r.content:
                status += f" {r.content[:24].hex()}"
        except Exception as exc:
            status = f"error {type(exc).__name__}: {exc}"
        time.sleep(args.wait)
        after = read_likes(tgt_uid)
        delta = (after - before) if isinstance(before, int) and isinstance(after, int) else None
        results.append((label, status, before, after, delta))
        print(f"  [{label:24}] liker {rec.get('uid')} -> {status} | "
              f"{before} -> {after} | {'+' + str(delta) if delta else 'no change'}")

    print("\nSUMMARY")
    winners = [r for r in results if r[4]]
    for label, status, before, after, delta in results:
        mark = "CREDITED" if delta else "ignored"
        print(f"  {mark:9} {label:24} {before}->{after}")
    if winners:
        print(f"\nRESULT: {winners[0][0]} is the working shape")
        return 0
    print("\nRESULT: none of the four shapes moved the counter - the body is not "
          "the problem; the liker account itself is not being credited")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
