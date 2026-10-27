"""Fast like delivery diagnostic (sample-based, time-boxed): does the pool still
deliver likes to the owner's UID? Triggered by run.sh when like_diag.flag is enabled.
Commits the report and disables the flag (in the repo, so it truly runs once)."""
import sys, json, time, os, random, warnings
from concurrent.futures import ThreadPoolExecutor
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.path.insert(0, "guests_manager")
import httpx
import ff_bd_api as api

TARGET = 9078648985  # owner's FF UID
TIME_BUDGET = 240   # seconds for JWT minting
log = []
def say(m):
    print(m, flush=True); log.append(str(m))

# ---- collect candidate credentials (sample) ----
cands = []
try:
    regs = json.load(open("guests_manager/registered_pool.json"))
    reg_bd = [(str(r["uid"]), r["pw"]) for r in regs if r.get("pw") and str(r.get("region","")).upper() == "BD"]
    random.shuffle(reg_bd)
    cands += reg_bd[:25]
except Exception:
    pass
try:
    cands += api.load_creds()[:15]
except Exception:
    pass
try:
    ph = json.load(open("phone_accounts.json"))
    cands += [(str(r["uid"]), r["pw"]) for r in ph if r.get("uid") and r.get("pw")][:15]
except Exception:
    pass
# always include the newest registered account
try:
    regs = json.load(open("guests_manager/registered_pool.json"))
    if regs and regs[-1].get("pw"): cands.append((str(regs[-1]["uid"]), regs[-1]["pw"]))
except Exception:
    pass
say(f"candidates sampled: {len(cands)}")

t0 = time.time()
def try_mint(up):
    if time.time() - t0 > TIME_BUDGET:
        return None
    try:
        return api.mint_jwt(up[0], up[1])
    except Exception:
        return None

toks = []
with ThreadPoolExecutor(max_workers=8) as ex:
    for t in ex.map(try_mint, cands):
        if t and t not in toks:
            toks.append(t)
say(f"valid tokens minted: {len(toks)} / {len(cands)}")

report = {"ts": int(time.time()), "candidates": len(cands), "tokens": len(toks), "landed": None, "log": log}
if toks:
    jwt = toks[0]["token"]
    s0 = api.get_stats(TARGET, jwt)
    before = s0.get("likes") if s0 else None
    say(f"target likes before: {before}")
    if before is not None:
        import like_pb2 as lpb
        sent = 0
        for t in toks:
            m = lpb.like(); m.uid = TARGET; m.region = "BD"
            try:
                r = httpx.post(api.LIKE_URL, headers=api._headers(t["token"], "OB55"),
                               data=api._enc(m.SerializeToString()), timeout=20, verify=False)
                if r.status_code == 200:
                    sent += 1
            except Exception:
                pass
        say(f"sends accepted (HTTP 200): {sent}")
        time.sleep(6)
        s1 = api.get_stats(TARGET, jwt)
        after = s1.get("likes") if s1 else None
        delta = (after - before) if isinstance(after, int) else None
        say(f"target likes after: {after} | LANDED: {delta}")
        report["landed"] = delta; report["sent"] = sent
    else:
        say("cannot read target stats — sample tokens cannot query profile")
else:
    say("NO valid tokens — whole sample failed login (accounts dead/blocked)")

json.dump(report, open("like_diag_report.txt", "w"), indent=1)

# ---- commit report + disable flag in the repo (so it truly runs once) ----
tok = os.environ.get("GITHUB_USAGE_TOKEN")
if tok:
    try:
        import urllib.request, base64
        def put(path, content, msg):
            url = f"https://api.github.com/repos/selimsb645-max/ff-like-bot/contents/{path}"
            sha = None
            try:
                r = urllib.request.urlopen(urllib.request.Request(url,
                    headers={"Authorization": f"token {tok}", "User-Agent": "ff"}), timeout=15)
                sha = json.loads(r.read()).get("sha")
            except Exception:
                pass
            body = json.dumps({"message": msg,
                "content": base64.b64encode(content.encode()).decode(),
                **({"sha": sha} if sha else {})}).encode()
            urllib.request.urlopen(urllib.request.Request(url, method="PUT", data=body,
                headers={"Authorization": f"token {tok}", "User-Agent": "ff",
                         "Content-Type": "application/json"}), timeout=20)
        put("like_diag_report.txt", json.dumps(report, indent=1), "diag: like delivery report")
        put("like_diag.flag", '{"enabled": false}', "diag: disable one-shot flag")
    except Exception as e:
        say(f"report commit failed: {str(e)[:80]}")
