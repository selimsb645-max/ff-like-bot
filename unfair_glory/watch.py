#!/usr/bin/env python3
"""Live progress watcher for the glory farm (reads the local dashboard API).

    python3 watch.py            # one snapshot
    python3 watch.py --loop 60  # refresh every 60s

Shows per-account status/level/exp/matches so you can tell whether matches are
actually COMPLETING (matches_completed > 0) instead of just "SEARCHING".
"""
import argparse
import json
import time
import urllib.request

URL = "http://127.0.0.1:25567/api/state"


def snapshot():
    try:
        with urllib.request.urlopen(URL, timeout=8) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"cannot reach dashboard on 25567 ({e}) - is `python3 auto_start.py ...` running?")
        return None


def render(st):
    accts = st.get("accounts") or []
    print(f"{'nickname':<14}{'account_id':<14}{'status':<20}{'lvl':>4}{'exp':>9}{'matches':>8}")
    print("-" * 70)
    total_exp = total_m = 0
    for a in accts:
        exp = a.get("exp") or a.get("current_exp") or 0
        m = a.get("matches") or a.get("matches_completed") or 0
        try:
            total_exp += int(exp)
            total_m += int(m)
        except Exception:
            pass
        print(f"{str(a.get('nickname',''))[:13]:<14}"
              f"{str(a.get('account_id',''))[:13]:<14}"
              f"{str(a.get('status',''))[:19]:<20}"
              f"{str(a.get('level','')):>4}{str(exp):>9}{str(m):>8}")
    print("-" * 70)
    print(f"accounts={len(accts)}  total_exp={total_exp}  matches_completed={total_m}"
          f"  creating={st.get('creating')}")
    prog = st.get("create_progress") or []
    if prog:
        print("last create step:", str(prog[-1])[:110])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="refresh every N seconds")
    args = ap.parse_args()
    while True:
        st = snapshot()
        if st:
            print(f"\n=== {time.strftime('%H:%M:%S')} ===")
            render(st)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
