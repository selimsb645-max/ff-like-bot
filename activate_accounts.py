#!/usr/bin/env python3
"""Enter the game once for every pooled account so its likes actually count.

A guest that has never called GetLoginData has no in-game profile: LikeProfile still
answers 200 but the like lands nothing, and get_stats reports BR_ACCOUNT_NOT_FOUND.
This re-grants the guest (safe: the grant pair is not single-use), then posts
GetLoginData with the JWT we already hold, which creates the profile.

    LIKE_PROXY_URL=http://127.0.0.1:8899 python3 activate_accounts.py [--limit N]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ff_bd_api as api


def get_login_data(token: dict):
    """GetLoginData with the JWT already minted; the phone/proxy path stays the same."""
    pair, why = api.grant_any(str(token["uid"]), token.get("pass", ""))
    if not pair:
        return None, f"grant failed: {why}"
    at2, oid2 = pair
    wire = (api._pf_str(22, oid2) + api._pf_str(23, "4")
            + api._pf_str(29, at2) + api._pf_str(99, "4"))
    h = {"User-Agent": api.LOGIN_UA, "Connection": "Keep-Alive", "Accept-Encoding": "gzip",
         "X-GA-SV": str(int(time.time())),
         "Content-Type": "application/x-www-form-urlencoded", "Expect": "100-continue",
         "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1", "ReleaseVersion": "OB55",
         "Authorization": f"Bearer {token['token']}"}
    for attempt in range(3):
        try:
            r = api._post_any(token["server_url"].rstrip("/") + "/GetLoginData",
                              headers=h, data=api._enc(wire), timeout=25)
            return (r.status_code, len(r.content)), ""
        except Exception as ex:
            if attempt == 2:
                return None, f"{type(ex).__name__}"
            time.sleep(3)


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 99
    pool_path = Path(__file__).parent / "bd_pool.json"
    pool = json.loads(pool_path.read_text())
    cache = api.ready_uids()
    done = 0
    for t in pool[:limit]:
        res, err = get_login_data(t)
        ok = bool(res and res[0] == 200)
        print(f"{t['uid']}  entry: " + (f"HTTP {res[0]}, {res[1]} bytes" if res else err), flush=True)
        time.sleep(2)
        stats = api.get_stats(int(t["uid"]), t["token"])
        good = bool(stats and not stats.get("error"))
        print(f"        profile: " + (f"OK name={stats.get('name')} likes={stats.get('likes')}"
                                     if good else str(stats)), flush=True)
        if ok and good:
            done += 1
            cache[str(t["uid"])] = {"ready": True, "ts": time.time()}
            t["activated"] = True
        else:
            cache[str(t["uid"])] = {"ready": False, "ts": time.time()}
        time.sleep(2)
    api.READY_FILE.write_text(json.dumps(cache, indent=1))
    pool_path.write_text(json.dumps(pool, indent=1))
    print(f"activated {done}/{min(limit, len(pool))}")


if __name__ == "__main__":
    main()
