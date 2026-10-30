"""Phone-side diagnostic: prints the exact fields that go into the TCP auth token.

Usage:  python3 diag_token.py            # newest account that still logs in
        python3 diag_token.py 8373923878 # a specific uid
"""
import asyncio, json, os, sys, time
os.environ.setdefault("BD_TUNNEL", "1")
from levelbot import run_level as rl


def candidates():
    if len(sys.argv) > 1:
        return [(sys.argv[1], "")]
    pool = json.load(open("guests_manager/registered_pool.json"))
    pool = [r for r in pool if str(r.get("uid", "")).isdigit()]
    pool.sort(key=lambda r: int(r["uid"]), reverse=True)
    return [(str(r["uid"]), r["pw"]) for r in pool[:6]]


login = None
used = None
for uid, pw in candidates():
    print(f"trying {uid}...", flush=True)
    login = rl._jwt_cache_get(uid) or rl.raw_login(uid, pw or os.environ.get("GUEST_PW", ""))
    if login:
        used = uid
        break
if not login:
    print("LOGIN FAILED for every candidate")
    sys.exit(1)

now = int(time.time())
print("OK uid     :", used)
print("ts field   :", login["ts"], "| now:", now, "| diff(s):", now - login["ts"])
print("account_id :", login["account_id"], "| matches uid:", str(login["account_id"]) == str(used))
print("key/iv     :", type(login["key"]).__name__, len(login["key"]),
      type(login["iv"]).__name__, len(login["iv"]))
tok = asyncio.run(rl.build_conn_token(login))
print("token len  :", len(tok))
print("token head :", tok[:48])
