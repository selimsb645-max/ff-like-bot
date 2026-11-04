#!/usr/bin/env python3
"""Create accounts slowly and let them age — Garena may only count likes from
accounts that are not brand new, so the pool we test tomorrow has to be made today."""
import json, sys, time, warnings
warnings.filterwarnings("ignore"); sys.path.insert(0, ".")
import supply
from guests_manager import game_register as gr

want = int(sys.argv[1]) if len(sys.argv) > 1 else 8
gap = int(sys.argv[2]) if len(sys.argv) > 2 else 90
pool = json.load(open("bd_pool_owned.json"))
have = {r["uid"] for r in pool}
sess = gr.new_session()
made = 0
while made < want:
    rec = supply.create_one(sess, tries=1)
    if not rec:
        print("throttled — waiting 150s", flush=True)
        time.sleep(150)
        continue
    if rec["uid"] in have:
        continue
    j = json.dumps(pool)
    pool.append(rec)
    json.dump(pool, open("bd_pool_owned.json", "w"), indent=1)
    have.add(rec["uid"])
    made += 1
    print(f"[{made}/{want}] {rec['uid']} {rec['nick']} token={len(rec['token'])}", flush=True)
    time.sleep(gap)
print("done", flush=True)
