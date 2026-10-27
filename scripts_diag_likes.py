"""One-shot like diagnostic: does the pool still deliver likes to the owner's UID?
Run via .github/workflows/diag.yml. Reports: pool size, activated count, likes before/after."""
import sys, json, time, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.path.insert(0, "guests_manager")
import ff_bd_api as api

TARGET = 9078648985  # owner's FF UID
print("building pool...", flush=True)
pool = api.get_pool()
print(f"pool size: {len(pool)}", flush=True)
jwt = pool[0]["token"] if pool else None
s0 = api.get_stats(TARGET, jwt) if jwt else None
before = s0.get("likes") if s0 else None
print(f"target likes before: {before}", flush=True)
if before is None:
    print("cannot read target stats — aborting", flush=True)
    sys.exit(0)

cache = api.ready_uids()
act = [t for t in pool if api.is_activated(t, cache)]
print(f"activated senders: {len(act)}", flush=True)
api.READY_FILE.write_text(json.dumps(cache, indent=1))

sent = api.send_like(TARGET, count=len(act))
print(f"sends accepted (HTTP 200): {sent}", flush=True)
time.sleep(5)
s1 = api.get_stats(TARGET, jwt)
after = s1.get("likes") if s1 else None
delta = (after - before) if isinstance(after, int) else None
print(f"target likes after: {after} | LANDED: {delta}", flush=True)
