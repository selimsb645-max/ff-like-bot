"""Like delivery diagnostic: does the pool still deliver likes to the owner's UID?
Triggered by run.sh when like_diag.flag exists. Self-disables + commits a report."""
import sys, json, time, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.path.insert(0, "guests_manager")
import ff_bd_api as api

TARGET = 9078648985  # owner's FF UID
log = []

def say(m):
    print(m, flush=True)
    log.append(str(m))

say("building pool...")
pool = api.get_pool()
say(f"pool size: {len(pool)}")
jwt = pool[0]["token"] if pool else None
s0 = api.get_stats(TARGET, jwt) if jwt else None
before = s0.get("likes") if s0 else None
say(f"target likes before: {before}")

act = 0
delta = None
if before is not None:
    cache = api.ready_uids()
    act = len([t for t in pool if api.is_activated(t, cache)])
    say(f"activated senders: {act}")
    try:
        api.READY_FILE.write_text(json.dumps(cache, indent=1))
    except Exception:
        pass
    sent = api.send_like(TARGET, count=min(act, 500))
    say(f"sends accepted (HTTP 200): {sent}")
    time.sleep(5)
    s1 = api.get_stats(TARGET, jwt)
    after = s1.get("likes") if s1 else None
    delta = (after - before) if isinstance(after, int) else None
    say(f"target likes after: {after} | LANDED: {delta}")
else:
    say("cannot read target stats — pool tokens dead or endpoint blocked")

report = {"ts": int(time.time()), "pool": len(pool), "activated": act, "landed": delta,
         "log": log}
open("like_diag_report.txt", "w").write(json.dumps(report, indent=1))

# commit report + disable flag, best-effort via GitHub API (same pattern as bot.py)
tok = os.environ.get("GITHUB_USAGE_TOKEN")
if tok:
    try:
        import urllib.request
        def put(path, content, msg):
            url = f"https://api.github.com/repos/selimsb645-max/ff-like-bot/contents/{path}"
            sha = None
            try:
                r = urllib.request.urlopen(urllib.request.Request(url,
                    headers={"Authorization": f"token {tok}", "User-Agent": "ff"}), timeout=15)
                sha = json.loads(r.read()).get("sha")
            except Exception:
                pass
            body = json.dumps({"message": msg, "content":
                __import__("base64").b64encode(content.encode()).decode(), **({"sha": sha} if sha else {})}).encode()
            req = urllib.request.Request(url, method="PUT", data=body,
                headers={"Authorization": f"token {tok}", "User-Agent": "ff",
                         "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20)
        put("like_diag_report.txt", json.dumps(report, indent=1), "diag: like delivery report")
    except Exception as e:
        say(f"report commit failed: {str(e)[:80]}")
try:
    os.remove("like_diag.flag")
except Exception:
    pass
