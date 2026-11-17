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



def send_direct(acct, target, variant="uid"):
    """One LikeProfile from ONE account, so the result is not diluted by the pool.

    send_like() fans out over every pool row, which is why earlier runs could not
    tell a good liker from a bad one.
    """
    import like_pb2 as lpb
    m = lpb.like()
    m.uid = int(target)
    if variant != "noregion":
        m.region = "BD"
    body = api._enc(m.SerializeToString())
    base = api._api_base(acct)
    r = api._client().post(base + "/LikeProfile",
                           headers=api._headers(acct["token"], "OB55"), data=body)
    return r.status_code, r.content


def newest_bd_account():
    """Newest pool record that can send a like.

    Pool rows are written by two different writers and do NOT share a schema:
    supply.py rows carry uid/token/pass/region/server_url, while guestgen rows
    also carry hex key/iv. Likes only need a JWT, so the only hard requirements
    here are a token and a BD region.
    """
    raw = json.loads(Path("bd_pool.json").read_text())
    recs = raw if isinstance(raw, list) else list(raw.values())
    total = len(recs)
    usable = [r for r in recs
              if isinstance(r, dict) and str(r.get("region", "BD")).upper() == "BD"
              and r.get("token") and (r.get("pass") or r.get("pw"))]
    if not usable:
        print(f"  pool has {total} record(s) but none usable for a like "
              f"(need region BD + token + password)")
        return None
    usable.sort(key=lambda r: r.get("ts") or r.get("exp") or 0, reverse=True)
    print(f"  pool: {total} record(s), {len(usable)} usable")
    return usable[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", type=int, default=9078648985, help="target account id")
    ap.add_argument("--count", type=int, default=10, help="likes to attempt")
    ap.add_argument("--direct", action="store_true",
                    help="send exactly one LikeProfile from the forced liker, bypassing the pool")
    ap.add_argument("--variant", default="uid", choices=["uid", "noregion"],
                    help="payload shape: uid+BD region (default) or uid with no region field")
    ap.add_argument("--liker", default=None,
                    help="force one pool account as the sender (uid); default: newest usable")
    args = ap.parse_args()

    if args.liker:
        pool = json.loads(Path("bd_pool.json").read_text())
        recs = pool if isinstance(pool, list) else list(pool.values())
        acct = next((r for r in recs if str(r.get("uid")) == str(args.liker)), None)
        if not acct:
            print(f"liker {args.liker} is not in bd_pool.json")
            return 2
    else:
        acct = newest_bd_account()
    if not acct:
        print("no BD account with a token in bd_pool.json")
        return 2
    print(f"liker : {acct['uid']}  (minted {acct.get('ts_human', '?')})")
    print(f"target: {args.uid}")

    before, src = read_likes(args.uid)
    # a like from an account locked to another region never credits a BD target
    try:
        import base64 as _b64
        _p = acct["token"].split(".")[1]
        _p += "=" * (-len(_p) % 4)
        _cl = json.loads(_b64.urlsafe_b64decode(_p))
        print(f"  liker claims: lock_region={_cl.get('lock_region')} "
              f"country={_cl.get('country_code')} account_id={_cl.get('account_id')}")
    except Exception as _e:
        print(f"  liker claims unreadable: {type(_e).__name__}")

    try:
        me = api.get_profile(int(acct.get("account_id") or acct["uid"])) or {}
        print(f"  liker profile: region={me.get('region')} level={me.get('level')} "
              f"likes={me.get('likes')} name={me.get('name')}")
    except Exception as _e:
        print(f"  liker profile unreadable: {type(_e).__name__}")

    print(f"before : {before}  (via {src})")

    if args.direct:
        st, body = send_direct(acct, args.uid, args.variant)
        print(f"direct like: variant={args.variant} http={st} body={body[:24].hex() or 'empty'}")
        sent = 1 if st == 200 else 0
    else:
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
    # লাইসেন্স + লগইন গেট (guard/gate.py)
    from guard.gate import enforce as _enforce
    _enforce("like_canary.py")

    raise SystemExit(main())
