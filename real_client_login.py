#!/usr/bin/env python3
"""REAL CLIENT LOGIN — the one experiment that decides the whole like path.

Everything we talk about rests on one untested sentence: "a guest account created
by the real game client delivers likes." This script takes such a credential (the
uid/password the client wrote into its .dat file), logs it in with the full-device
payload, reads its profile, and stores it in the pool so like_canary can use it.

It must run through the phone (BD residential IP): a guest's region is stamped at
login, so logging in from a datacenter would turn it into a US account and waste it.

    BD_PROXY_URL=http://127.0.0.1:8899 python3 real_client_login.py --check-ip
    BD_PROXY_URL=http://127.0.0.1:8899 python3 real_client_login.py
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "guests_manager"))

import full_login                      # noqa: E402
import ff_bd_api as api                # noqa: E402
from guests_manager import game_register as gr   # noqa: E402

POOL = ROOT / "bd_pool.json"
REAL = ROOT / "real_client" / "pool.json"


def load_real():
    if not REAL.exists():
        return []
    try:
        data = json.loads(REAL.read_text())
    except Exception:
        return []
    return data if isinstance(data, list) else list(data.values())


def exit_ip():
    try:
        r = gr.new_session().get("https://api.ipify.org", timeout=15)
        return r.text.strip()
    except Exception as exc:
        return f"unreachable ({type(exc).__name__})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", default=None, help="only this uid")
    ap.add_argument("--check-ip", action="store_true", help="print the exit IP and stop")
    ap.add_argument("--force", action="store_true", help="re-login even if already stored")
    a = ap.parse_args()

    if a.check_ip:
        ip = exit_ip()
        print(f"exit ip: {ip}")
        return 0 if ip and not ip.startswith("unreachable") else 2

    recs = [r for r in load_real() if not a.uid or str(r.get("uid")) == a.uid]
    if not recs:
        print("no real-client credentials in real_client/pool.json")
        return 2

    try:
        pool = json.loads(POOL.read_text())
        if not isinstance(pool, list):
            pool = list(pool.values())
    except Exception:
        pool = []

    sess = gr.new_session()
    made = 0
    for rec in recs:
        uid, pw = str(rec["uid"]), rec["pass"]
        have = next((p for p in pool if str(p.get("uid")) == uid and p.get("token")), None)
        if have and not a.force:
            print(f"{uid}: already in the pool with a token, skipping (use --force)")
            continue

        pair = gr.token_grant(sess, uid, pw)
        if not pair or not pair[0]:
            print(f"{uid}: grant refused - {pair}")
            continue
        at, oid = pair
        print(f"{uid}: grant ok (open_id {str(oid)[:8]}...)")

        info = full_login.full_major_login(uid, oid, at, region="BD")
        if not info or not info.get("jwt"):
            print(f"{uid}: MajorLogin gave no JWT")
            continue
        jwt = info["jwt"]
        srv = str(info.get("server_url") or info.get("url") or "").rstrip("/")
        acct_id = info.get("account_id") or uid
        print(f"{uid}: jwt {len(jwt)} chars · host {srv}")

        key = info.get("key")
        iv = info.get("iv")
        entry = {
            "uid": uid, "pass": pw, "token": jwt,
            "key": key.hex() if isinstance(key, (bytes, bytearray)) else key,
            "iv": iv.hex() if isinstance(iv, (bytes, bytearray)) else iv,
            "account_id": acct_id, "server_url": srv, "region": "BD",
            "source": "real-client", "profiled": True, "ts": int(time.time()),
        }
        prof = api.get_profile(int(acct_id), jwt) or {}
        entry["live_name"] = prof.get("name")
        entry["live_level"] = prof.get("level")
        entry["live_region"] = prof.get("region")
        entry["verified"] = bool(prof.get("level") is not None or prof.get("name"))
        print(f"    profile: name={prof.get('name')} region={prof.get('region')} "
              f"level={prof.get('level')} likes={prof.get('likes')}")

        pool = [p for p in pool if str(p.get("uid")) != uid] + [entry]
        POOL.write_text(json.dumps(pool, indent=2))
        try:
            POOL.with_suffix(".backup.json").write_text(json.dumps(pool, indent=2))
        except Exception:
            pass
        made += 1

    print(f"stored {made} account(s); pool size {len(pool)}")
    return 0 if made or pool else 1


if __name__ == "__main__":
    raise SystemExit(main())
