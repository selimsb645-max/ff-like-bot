#!/usr/bin/env python3
"""DARK MASTER PROBE — do the accounts a working bot used still log in, and do
their likes count?

The UNFAIR build's token_cache held 20 BD guest accounts with a `likes` column that
only a real working bot could have filled, so those accounts are the best available
evidence that likes CAN be credited. We never tried them with the full-device login
that fixed our own pipeline. This does that, one account at a time, and optionally
spends one like from each so the target counter can prove or disprove it.

    BD_PROXY_URL=http://127.0.0.1:8906 python3 darkmaster_probe.py --limit 3
    BD_PROXY_URL=http://127.0.0.1:8906 python3 darkmaster_probe.py --limit 1 --like 9078648985
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "guests_manager"))

import full_login                       # noqa: E402
import ff_bd_api as api                 # noqa: E402
import like_pb2 as lpb                  # noqa: E402
from guests_manager import game_register as gr   # noqa: E402

CANDIDATES = ROOT / "bd_candidates_darkmaster.json"
RESULTS = ROOT / "darkmaster_probe.json"


def load():
    data = json.loads(CANDIDATES.read_text())
    return data if isinstance(data, list) else list(data.values())


def likes_of(jwt, uid):
    wire = api._pb(1, uid) + api._pb(2, 1)
    r = api._client().post(api.profile_url(), headers=api._headers(jwt),
                           content=api._enc(wire), timeout=25)
    if r.status_code != 200:
        return None
    top = api._pb_walk(r.content)
    basic = api._pb_walk(api._first(top, 1)) if api._first(top, 1) else {}
    return api._first(basic, 21)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--like", type=int, default=0, help="target uid to like from each")
    ap.add_argument("--wait", type=int, default=60)
    a = ap.parse_args()

    recs = [r for r in load() if r.get("uid") and r.get("pass")][: a.limit]
    if not recs:
        print("কোনো প্রার্থী অ্যাকাউন্ট নেই")
        return 2

    sess = gr.new_session()
    out = []
    for r in recs:
        uid, pw = str(r["uid"]), r["pass"]
        row = {"uid": uid}
        pair = gr.token_grant(sess, uid, pw)
        if not pair or not pair[0]:
            row["grant"] = "refused"
            print(f"{uid}: grant বন্ধ")
            out.append(row)
            continue
        row["grant"] = "ok"
        info = full_login.full_major_login(uid, pair[1], pair[0], region="BD")
        if not info or not info.get("jwt"):
            row["login"] = "no jwt"
            print(f"{uid}: grant ঠিক, কিন্তু MajorLogin টোকেন দিল না")
            out.append(row)
            continue
        jwt = info["jwt"]
        row["jwt_len"] = len(jwt)
        row["server_url"] = info.get("server_url")
        prof = api.get_profile(int(info.get("account_id") or uid), jwt) or {}
        row["name"] = prof.get("name")
        row["region"] = prof.get("region")
        row["level"] = prof.get("level")
        print(f"{uid}: লগইন ঠিক · {len(jwt)} অক্ষর · {row['name']} · {row['region']} · Lv{row['level']}")

        if a.like:
            before = likes_of(jwt, a.like)
            m = lpb.like()
            m.uid = int(a.like)
            m.region = "BD"
            h = api._headers(jwt, "OB55")
            h["Content-Type"] = "application/octet-stream"
            rr = api._client().post(api._like_url(), headers=h,
                                    content=api._enc(m.SerializeToString()), timeout=25)
            print(f"    লাইক → {rr.status_code} · লক্ষ্যের কাউন্টার {before}")
            time.sleep(a.wait)
            after = likes_of(jwt, a.like)
            row["like_before"], row["like_after"] = before, after
            print(f"    +{a.wait}s → {after} {'✔ বেড়েছে' if before is not None and after and after > before else '✘ বাড়েনি'}")
        out.append(row)

    RESULTS.write_text(json.dumps(out, indent=1))
    ok = sum(1 for r in out if r.get("jwt_len"))
    print(f"\n{ok}/{len(out)} অ্যাকাউন্ট লগইন করতে পেরেছে · ফলসংরক্ষিত {RESULTS.name}")
    return 0 if ok else 1


if __name__ == "__main__":
    # লাইসেন্স + লগইন গেট (guard/gate.py)
    from guard.gate import enforce as _enforce
    _enforce("darkmaster_probe.py")

    raise SystemExit(main())
