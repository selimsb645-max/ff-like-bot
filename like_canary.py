#!/usr/bin/env python3
"""LIKE CANARY — does a freshly minted BD account actually move the like counter?

The question this answers, and nothing else: LikeProfile answers 200 either way,
so "sent" is not proof. This reads the target's counter, sends N likes, then
re-reads the counter at 0 / 60 / 150 seconds. Only a rising number is proof.

    python3 like_canary.py --uid 9078648985 --count 10

Run it on the phone (residential BD IP). Nothing is written to the pool.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "guests_manager")

import ff_bd_api as api  # noqa: E402


def read_likes(uid):
    """Return (likes, source) using whichever reader this build exposes."""
    for name in ("profile_stats", "live_profile", "personal_show", "get_profile"):
        fn = getattr(api, name, None)
        if not fn:
            continue
        try:
            d = fn(uid)
        except Exception as exc:
            print(f"  [{name}] {type(exc).__name__}: {exc}")
            continue
        if isinstance(d, dict) and d.get("likes") is not None:
            return int(d["likes"]), name
    return None, None


def newest_bd_account():
    pool = json.loads(Path("bd_pool.json").read_text())
    for rec in sorted(pool, key=lambda r: r.get("ts") or 0, reverse=True):
        if str(rec.get("region", "")).upper() != "BD":
            continue
        if rec.get("token") and rec.get("key"):
            return rec
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", type=int, default=9078648985, help="target account id")
    ap.add_argument("--count", type=int, default=10, help="likes to attempt")
    args = ap.parse_args()

    acct = newest_bd_account()
    if not acct:
        print("no BD account with a token in bd_pool.json")
        return 2
    print(f"liker : {acct['uid']}  (minted {acct.get('ts_human', '?')})")
    print(f"target: {args.uid}")

    before, src = read_likes(args.uid)
    print(f"before : {before}  (via {src})")

    sent = api.send_like(args.uid, count=args.count)
    print(f"posted : {sent} likes · {json.dumps(api.LAST_LIKE_STATS.get('reasons', {}))}")

    marks = []
    for wait in (0, 60, 150):
        if wait:
            time.sleep(60 if wait == 60 else 90)
        now, _ = read_likes(args.uid)
        marks.append(now)
        print(f"+{wait:>3}s : {now}")

    delta = None
    if before is not None and marks and marks[-1] is not None:
        delta = marks[-1] - before
    print("\nRESULT:", "INCREASED by %d" % delta if delta else "no change")
    if not delta:
        print("likes are being accepted but not counted - the liker account is not")
        print("being credited (this is the real bug, not a delivery failure).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
