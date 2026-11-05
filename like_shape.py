#!/usr/bin/env python3
"""LIKE SHAPE — the body is documented; the transport is what we never varied.

Facts so far (2026-11-05): a verified BD, level-2, active-profile account sends
like{uid, region:"BD"} and gets HTTP 200 with an EMPTY body; the counter does not
move. The same 200-empty comes back for a UID that does not exist, so 200 proves
nothing - the server is ignoring the request, not crediting it.

This keeps the body fixed and varies the one thing left untested: how the bytes
are presented. One liker, one target, one request per variant, counter read after
each, so a rise can be attributed to a specific shape.

    python3 like_shape.py --liker 8410381093 --uid 9078648985

Run on the phone. Nothing is written to the pool.
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


def body_for(uid, region="BD", with_region=True, extra=None):
    m = lpb.like()
    m.uid = int(uid)
    if with_region:
        m.region = region
    return api._enc(m.SerializeToString())


def show_body(uid):
    m = lpb.like()
    m.uid = int(uid)
    m.region = "BD"
    print(f"  body: plain {m.SerializeToString().hex()} -> enc {api._enc(m.SerializeToString())[:24].hex()}...")
    print(f"  proto fields: {[(f.name, f.number) for f in lpb.like.DESCRIPTOR.fields]}")


def read_likes(uid):
    try:
        d = api.get_profile(int(uid)) or {}
        return d.get("likes")
    except Exception as exc:
        print(f"  read failed: {type(exc).__name__}: {exc}")
        return None


def base_headers(token, rv="OB55"):
    h = api._headers(token, rv)
    return dict(h)


def variants():
    """(label, headers-override, body-builder kwargs)"""
    return [
        ("form-urlencoded  [today's shape]",
         {"Content-Type": "application/x-www-form-urlencoded", "Expect": "100-continue"}, {}),
        ("octet-stream, no Expect",
         {"Content-Type": "application/octet-stream", "Expect": ""}, {}),
        ("octet-stream, no region field",
         {"Content-Type": "application/octet-stream", "Expect": ""}, {"with_region": False}),
        ("octet-stream, Bearer in X-GA-AT too",
         {"Content-Type": "application/octet-stream", "Expect": ""}, {}),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--liker", required=True, help="pool uid to send from (must be active BD)")
    ap.add_argument("--uid", type=int, required=True, help="target uid (needs a readable counter)")
    ap.add_argument("--wait", type=int, default=60, help="seconds to wait after each variant")
    a = ap.parse_args()

    pool = json.loads(Path("bd_pool.json").read_text())
    recs = pool if isinstance(pool, list) else list(pool.values())
    me = next((r for r in recs if str(r.get("uid")) == str(a.liker)), None)
    if not me:
        print(f"liker {a.liker} not in bd_pool.json")
        return 2

    prof = api.get_profile(int(me.get("account_id") or me["uid"])) or {}
    print(f"liker : {me['uid']}  region={prof.get('region')} level={prof.get('level')} name={prof.get('name')}")
    if str(prof.get("region") or "").upper() != "BD":
        print("refusing: the liker is not a BD account, so a BD target can never be credited")
        return 3

    show_body(a.uid)
    before = read_likes(a.uid)
    print(f"before : {before}\n")

    base = api._api_base(me)
    print(f"host   : {base}")
    results = []
    for label, hdr, kw in variants():
        h = base_headers(me["token"])
        for k, v in hdr.items():
            if v == "":
                h.pop(k, None)
            else:
                h[k] = v
        if "X-GA-AT" in label:
            h["X-GA-AT"] = f"Bearer {me['token']}"
        payload = body_for(a.uid, **kw)
        try:
            r = api._client().post(base + "/LikeProfile", headers=h, data=payload)
            shape = f"http={r.status_code} len={len(r.content)} body={r.content[:24].hex() or 'empty'}"
        except Exception as exc:
            shape = f"{type(exc).__name__}: {exc}"
        now = read_likes(a.uid)
        delta = (now - before) if (now is not None and before is not None) else None
        results.append((label, shape, delta))
        print(f"  {label:38} {shape}")
        if delta:
            print(f"     -> CREDITED (+{delta})")
            break
        time.sleep(max(5, a.wait))

    print("\nSUMMARY")
    for label, shape, delta in results:
        verdict = f"CREDITED +{delta}" if delta else "ignored"
        print(f"  {verdict:14} {label}")
    if not any(d for _, _, d in results):
        print("\nnothing credited. The liker is a valid BD account, so the remaining")
        print("explanations are eligibility (level / no match played) or a required")
        print("live game session - not the request shape.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
