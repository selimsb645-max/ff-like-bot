"""Phone-side diagnostic: prints the exact fields that go into the TCP auth token."""
import asyncio, json, os, sys, time
os.environ.setdefault("BD_TUNNEL", "1")
from levelbot import run_level as rl

pool = json.load(open("guests_manager/registered_pool.json"))
pool.sort(key=lambda r: int(r["uid"]) if str(r["uid"]).isdigit() else 0, reverse=True)
acc = pool[0]
print("uid:", acc["uid"])
login = rl._jwt_cache_get(acc["uid"]) or rl.raw_login(acc["uid"], acc["pw"])
if not login:
    print("LOGIN FAILED"); sys.exit(1)
now = int(time.time())
print("ts field   :", login["ts"], "| now:", now, "| diff(s):", now - login["ts"])
print("account_id :", login["account_id"], "| matches uid:", str(login["account_id"]) == str(acc["uid"]))
print("key/iv     :", type(login["key"]).__name__, len(login["key"]), type(login["iv"]).__name__, len(login["iv"]))
tok = asyncio.run(rl.build_conn_token(login))
print("token len  :", len(tok))
print("token head :", tok[:48])
print("token tail :", tok[-24:])
