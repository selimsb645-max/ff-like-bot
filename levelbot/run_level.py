"""
FF Level/Glory Farm Runner — auth via our proven OB55 recipe, TCP match engine
from the levelbot package. Runs N accounts in parallel, Lone Wolf solo mode.

Usage:
  python -m levelbot.run_level --all 4 --cycles 60
  python -m levelbot.run_level --uid 8051557932 --cycles 10
"""

import argparse
import asyncio
import base64
import json
import random
from datetime import datetime, timezone
import os
import re
import sys
import time
from pathlib import Path
ROOT = Path(__file__).parent.parent

import httpx
import urllib3
urllib3.disable_warnings()

sys.path.insert(0, str(Path(__file__).parent.parent))
import ff_bd_api as api

# --- BD proxy support: MajorLogin returns "Protection Bypass" from non-BD IPs.
# run.sh sets BD_PROXY_URL when the BD tunnel is down but a BD proxy was found.
def _with_px(kw):
    px = (os.environ.get("BD_PROXY_URL") or "").strip()
    if px and "proxy" not in kw:
        kw["proxy"] = px
    return kw

_ox_post, _ox_get, _ox_put, _ox_client = httpx.post, httpx.get, httpx.put, httpx.Client
httpx.post = lambda *a, **kw: _ox_post(*a, **_with_px(kw))
httpx.get = lambda *a, **kw: _ox_get(*a, **_with_px(kw))
httpx.put = lambda *a, **kw: _ox_put(*a, **_with_px(kw))
httpx.Client = lambda *a, **kw: _ox_client(*a, **_with_px(kw))  # our proven HTTP auth/pool module

from levelbot.xC4 import EnC_PacKeT, DecodE_HeX
from levelbot.connection import GameConnection
from levelbot.packet_builder import PacketBuilder
from levelbot.match_engine import MatchEngine

GLORY_WEBHOOK = "https://webhook.site/b80ac2d7-7cff-4cd4-bd3b-363a2829913f"

def wh_report(payload):
    """Live progress report to webhook (readable while job still runs)."""
    try:
        import urllib.request
        req = urllib.request.Request(GLORY_WEBHOOK, method="POST",
            data=json.dumps(payload, default=str).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass

def gh_commit(path, content, msg):
    """Best-effort commit to repo (observability channel that can't be rate-limited away)."""
    tok = os.environ.get("GITHUB_USAGE_TOKEN")
    if not tok:
        return
    try:
        import urllib.request
        api_url = f"https://api.github.com/repos/selimsb645-max/ff-like-bot/contents/{path}"
        sha = None
        try:
            sha = json.load(urllib.request.urlopen(urllib.request.Request(api_url,
                headers={"Authorization": f"token {tok}", "User-Agent": "ff"}), timeout=15)).get("sha")
        except Exception:
            pass
        body = json.dumps({"message": msg, "content": base64.b64encode(content.encode()).decode(),
                           "sha": sha, "branch": "main"}).encode()
        urllib.request.urlopen(urllib.request.Request(api_url, data=body, method="PUT",
            headers={"Authorization": f"token {tok}", "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json", "User-Agent": "ff"}), timeout=20)
    except Exception as e:
        print("gh_commit err:", str(e)[:80], flush=True)

def _heartbeat_thread():
    import threading
    def loop():
        n = 0
        while True:
            time.sleep(75)
            n += 1
            try:
                tail = ""
                try:
                    tail = open("farm_log.txt", "r", errors="replace").read()[-1200:]
                except Exception:
                    pass
                gh_commit("farm_status.json",
                    json.dumps({"ts": int(time.time()), "beat": n, "tail": tail}),
                    f"engine heartbeat {n}")
            except Exception:
                pass
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t

PROGRESS_FILE = Path(__file__).parent.parent / "level_progress.json"
GRANT_URL = "https://100067.connect.garena.com/api/v2/oauth/guest/token:grant"
ML_URL = "https://loginbp.ggpolarbear.com/MajorLogin"


def load_accounts(limit, prefer=None):
    """BD_CREDS_B64 env or repo bd_creds.txt -> list of (uid, pw)."""
    b64 = os.environ.get("BD_CREDS_B64", "")
    if b64:
        txt = base64.b64decode(b64).decode(errors="replace")
    else:
        p = Path(__file__).parent.parent / "bd_creds.txt"
        txt = p.read_text() if p.exists() else ""
    out = []
    for ln in txt.split():
        if ":" in ln:
            u, pw = ln.split(":", 1)
            if pw.isupper() and len(pw) == 64:  # self-minted guests only
                out.append((u, pw))
    # verified BD accounts from game_register pipeline FIRST (freshest working logins)
    try:
        rp = Path(__file__).parent.parent / "guests_manager" / "registered_pool.json"
        pool = []
        for r in json.loads(rp.read_text()):
            u, p = str(r.get("uid", "")).strip(), str(r.get("pw", "")).strip()
            if u and p:
                pool.append((u, p))
        out = pool + out
    except Exception:
        pass
    # merge phone-born BD accounts (any pw format, pushed by phone pipeline)
    pa = Path(__file__).parent.parent / "phone_accounts.json"
    try:
        known = {u for u, _ in out}
        for r in json.loads(pa.read_text()):
            u, p = str(r.get("uid", "")).strip(), str(r.get("pw", "")).strip()
            if u and p and u not in known:
                out.append((u, p))
                known.add(u)
    except Exception:
        pass
    # 2026-10-28: Garena now hard-blocks the pre-Oct guest pool ("Protection
    # Bypass"), so burned accounts must never be picked ahead of fresh ones.
    try:
        prog = load_progress()
    except Exception:
        prog = {}
    BURNED = {"majorlogin_failed", "logindata_failed"}
    usable, burned = [], []
    for u, pw in out:
        st = (prog.get(u) or {}).get("status")
        if st in BURNED and not (prog.get(u) or {}).get("cycles"):
            burned.append((u, pw))
        else:
            usable.append((u, pw))
    # freshest registrations sit at the END of the pool -> newest first
    usable.reverse()
    out = usable + burned
    if prefer:
        pref = [(u, pw) for u, pw in out if u in prefer]
        rest = [(u, pw) for u, pw in out if u not in prefer]
        out = pref + rest
    print(f"account pool: {len(usable)} usable, {len(burned)} burned-skip", flush=True)
    return out[:limit]


def _load_proxy_cache():
    try:
        return [u for u in json.loads((ROOT / "guests_manager" / "bd_proxy_cache.json").read_text())
                if isinstance(u, str) and not u.startswith("socks4")]
    except Exception:
        return []


def _save_proxy_cache(urls):
    try:
        (ROOT / "guests_manager" / "bd_proxy_cache.json").write_text(json.dumps(urls, indent=1))
    except Exception:
        pass


def hunt_bd_proxies(gr, max_n=8, extra=None):
    """Find multiple working BD-exit proxies that can actually reach Garena MSDK over TLS.
    Single proxies pass ipinfo but fail on garenanow.com, so verify reachability too."""
    from concurrent.futures import ThreadPoolExecutor
    cands = list(dict.fromkeys((gr.proxy_candidates() + (extra or []))))
    with ThreadPoolExecutor(max_workers=12) as ex:
        alive = list(ex.map(gr.tcp_alive, cands))
    cands = [c for c, a in zip(cands, alive) if a]
    print(f"bd proxy hunt: {len(cands)} alive candidates", flush=True)
    urls = []
    for url, ip, country in gr.ex_map_results(cands):
        if not url or country != "BD":
            continue
        if url.startswith("socks4"):
            continue  # socks4 proxies hang PySocks without honoring timeouts
        try:
            _ox_get("https://ffmconnect.live.gop.garenanow.com", proxy=url, timeout=8, verify=False)
        except Exception:
            print(f"bd proxy {url}: BD exit but can't reach garenanow — skip", flush=True)
            continue
        urls.append(url)
        print(f"bd proxy OK: {url} exit={ip}", flush=True)
        if len(urls) >= max_n:
            break
    return urls


FRESH_NICKS = {}


def save_fresh_to_pool(accounts):
    """Persist freshly minted (uid, pw) BD accounts into registered_pool.json so the
    like bot pool (ff_bd_api.load_registered_bd) picks them up automatically."""
    rp = ROOT / "guests_manager" / "registered_pool.json"
    try:
        try:
            data = json.loads(rp.read_text())
        except Exception:
            data = []
        if not isinstance(data, list):
            data = []
        have = {str(r.get("uid")) for r in data}
        added = 0
        for uid, pw in accounts:
            if str(uid) in have:
                continue
            data.append({"uid": str(uid), "pw": pw, "region": "BD",
                         "nick": FRESH_NICKS.get(str(uid), "Kexin"),
                         "verified": int(time.time())})
            added += 1
        if added:
            body = json.dumps(data, indent=1)
            rp.write_text(body)
            gh_commit("guests_manager/registered_pool.json", body,
                      f"pool: +{added} fresh BD account(s) for like bot")
        print(f"pool: {added} fresh account(s) saved to registered_pool.json", flush=True)
    except Exception as e:
        print(f"pool save EXC: {str(e)[:80]}", flush=True)


def mint_fresh_accounts(n, gr, proxies):
    """Mint n fresh BD-locked game accounts via the game_register pipeline.
    REQUIRES BD exit (tunnel up or BD proxy) — region locks permanently at MajorRegister.
    Rotates through the given BD proxy list, retrying each slot up to 4 times."""
    fresh = []
    tries = 0
    max_tries = max(n * 4, 4)
    while len(fresh) < n and tries < max_tries:
        tries += 1
        px = proxies[(tries - 1) % len(proxies)] if proxies else None
        if px:
            gr.ACTIVE_PROXY["url"] = px
            os.environ["BD_PROXY_URL"] = px
        else:
            gr.ACTIVE_PROXY["url"] = None
            os.environ.pop("BD_PROXY_URL", None)
        def _alarm(signum, frame):
            raise TimeoutError("mint attempt exceeded 120s")
        try:
            import signal
            signal.signal(signal.SIGALRM, _alarm)
            signal.alarm(120)
        except Exception:
            signal = None
        try:
            s = gr.new_session()
            reg = gr.msdk_register(s)
            if not reg:
                print(f"mint[t{tries}]: msdk_register failed via {px}", flush=True); continue
            (uid, pw), _ = reg
            tok = gr.token_grant(s, uid, pw)
            if not tok or not tok[0]:
                print(f"mint[t{tries}]: token_grant failed {uid}", flush=True); continue
            at, oid = tok
            nick = "Kexin" + "".join(random.choices("0123456789", k=4))
            ok, msg = gr.major_register(s, at, oid, nick)
            if not ok:
                print(f"mint[t{tries}]: major_register {uid}: {msg}", flush=True); continue
            ml = gr.major_login(s, at, oid)
            if not ml:
                print(f"mint[t{tries}]: major_login failed {uid}", flush=True); continue
            region = gr.jwt_region(ml)
            if region != "BD":
                print(f"mint[t{tries}]: {uid} region={region} != BD — discarded", flush=True); continue
            fresh.append((uid, pw))
            FRESH_NICKS[str(uid)] = nick
            print(f"mint[t{tries}]: FRESH BD ACCOUNT {uid} ({nick})", flush=True)
            wh_report({"mint": uid, "region": region, "nick": nick, "proxy": px, "ts": time.time()})
        except Exception as e:
            print(f"mint[t{tries}]: EXC {str(e)[:80]}", flush=True)
        finally:
            try:
                signal.alarm(0)
            except Exception:
                pass
        time.sleep(2)
    return fresh


FULL_ML_URL = "https://loginbp.ppmainecoonghj.com/MajorLogin"
DEVICES = [
    ("Symphony Z60", "Android OS 12 / API-31 (SP1A.210812.016)", "Mali-G57"),
    ("Walton Primo RX8", "Android OS 13 / API-33 (TP1A.220624.014)", "PowerVR GE8320"),
    ("Realme C55", "Android OS 13 / API-33 (TP1A.220624.014)", "Mali-G57 MC2"),
    ("Samsung SM-A145F", "Android OS 13 / API-33 (TP1A.220624.014)", "Mali-G68"),
    ("Xiaomi Redmi 12C", "Android OS 12 / API-31 (SP1A.210812.016)", "PowerVR GE8320"),
]
BD_IPS = ["103.4.146.10", "103.230.104.22", "103.108.148.15", "103.87.202.25",
          "103.100.88.10", "103.84.148.22", "103.102.28.10", "103.56.208.15"]
FULL_UAS = [
    "GarenaMSDK/4.0.44(25028RN03A ;Android 15;bn;BD;app 1.132.1 2019121229;)",
    "GarenaMSDK/4.0.43(25028RN03A ;Android 14;bn;BD;app 1.131.1 2019121229;)",
    "GarenaMSDK/4.0.45(25028RN03A ;Android 13;bn;BD;app 1.133.1 2019121229;)",
]


def _evb(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            return bytes(out)


def _pf_full(f, v):
    if isinstance(v, int):
        return _evb((f << 3)) + _evb(v)
    v = v.encode() if isinstance(v, str) else v
    return _evb((f << 3) | 2) + _evb(len(v)) + v


_CPU_BY_DEVICE = {
    "SM-A125F": "AArch64 Processor Cortex A55 | 1800 | 8",
    "Redmi 9A": "AArch64 Processor Cortex A53 | 2000 | 8",
    "POCO M4 Pro": "AArch64 Processor Cortex A76 | 2050 | 8",
    "RMX3085": "AArch64 Processor Cortex A73 | 2100 | 8",
}

def _stable_profile(uid):
    """Same uid -> same device fingerprint forever (stable identity, like a real phone)."""
    import uuid as _uuid
    rnd = random.Random(int(uid))
    model, os_s, gpu = rnd.choice(DEVICES)
    return {
        "model": model, "os_s": os_s, "gpu": gpu,
        "cpu": _CPU_BY_DEVICE.get(model, "AArch64 Processor Cortex A53 | 2000 | 8"),
        "device_id": f"Google|{_uuid.UUID(int=rnd.getrandbits(128))}",
        "ip": rnd.choice(BD_IPS),
        "mem": rnd.choice([5951, 6000, 6100, 5500]),
        "gpu_v": rnd.choice(["OpenGL ES 3.1 v1.46", "OpenGL ES 3.2 v1.1", "OpenGL ES 3.2 v1.2"]),
        "screen": rnd.choice([(1280, 720), (1600, 720), (720, 1600)]),
    }

def _full_device_wire(oid, at, uid, lang="bn"):
    pr = _stable_profile(uid)
    model, os_s, gpu = pr["model"], pr["os_s"], pr["gpu"]
    w, h = pr["screen"]
    fields = {
        3: datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        4: "free fire", 5: 4, 7: "1.132.1", 8: "2019116753",
        9: os_s, 10: "Handheld", 11: model, 12: w, 13: h, 14: "240",
        15: pr["cpu"],
        16: pr["mem"], 17: gpu,
        18: pr["gpu_v"], 19: pr["device_id"],
        20: pr["ip"], 21: lang, 22: oid, 23: "4", 24: "Handheld",
        25: model, 26: "BD", 29: at, 30: 1, 41: "Grameenphone", 42: "WIFI",
        92: pr["mem"], 93: "android_max",
        97: 1, 98: 1, 99: "4", 100: "4", 104: 77149, 105: 1,
    }
    return b"".join(_pf_full(k, v) for k, v in fields.items())


def _grant_direct(uid, pw):
    """guest_grant without the BD proxy: the phone tunnel intermittently kills TLS
    to the OAuth host, and the grant endpoint does not need a BD IP anyway."""
    saved = os.environ.pop("BD_PROXY_URL", "")
    try:
        pair = api.guest_grant(uid, pw)
    finally:
        if saved:
            os.environ["BD_PROXY_URL"] = saved
    if pair:
        return pair
    # last resort: retry through the proxy (some accounts only settle there)
    return api.guest_grant(uid, pw)


def raw_login(uid, pw):
    """guest grant + raw MajorLogin -> dict(jwt, key, iv, ts, url, region, account_id) or None."""
    pair = _grant_direct(uid, pw)
    at, oid = pair if pair else (None, None)
    if not at:
        return None
    # ATTEMPT 0 (2026-10-28): modern full-device MajorLogin. The old minimal wire
    # is answered with an encrypted "Protection Bypass" instead of a JWT.
    try:
        import ff_majorlogin
        try:
            _ip = os.environ.get("PUBLIC_IP", "").strip()
        except Exception:
            _ip = ""
        ml = ff_majorlogin.major_login(uid, oid, at, _ip or "223.191.51.89")
        if ml and ml.get("jwt"):
            ml["uid"] = str(uid)
            ml["pw"] = pw
            ml.setdefault("region", "BD")
            print(f"    raw_login[{uid}]: modern MajorLogin OK (jwt {len(ml['jwt'])} chars)", flush=True)
            return ml
        print(f"    raw_login[{uid}]: modern MajorLogin failed -> legacy wire fallbacks", flush=True)
    except Exception as _e:
        print(f"    raw_login[{uid}]: modern MajorLogin EXC {type(_e).__name__}: {str(_e)[:90]}", flush=True)
    wire = _full_device_wire(oid, at, uid)
    body = api._enc(wire)
    h = {"User-Agent": random.choice(FULL_UAS), "Accept-Encoding": "deflate, gzip",
         "X-GA-SV": "1789535859", "Authorization": f"Bearer {at}",
         "X-GA": "v1 1", "ReleaseVersion": "OB55",
         "Content-Type": "application/octet-stream",
         "X-Unity-Version": "2018.4.12f1"}
    r = httpx.post(FULL_ML_URL, headers=h, data=body, timeout=25, verify=False)
    if r.status_code != 200 or b"eyJ" not in r.content:
        # fallback: minimal login (old path)
        wire = (api._pf_str(22, oid) + api._pf_str(23, "4")
                + api._pf_str(29, at) + api._pf_str(99, "4"))
        h2 = {"User-Agent": api.LOGIN_UA, "Connection": "Keep-Alive",
              "Accept-Encoding": "gzip", "X-GA-SV": "1789638359",
              "Content-Type": "application/x-www-form-urlencoded", "Expect": "100-continue",
              "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1", "ReleaseVersion": "OB55"}
        r = httpx.post(ML_URL, headers=h2, data=api._enc(wire), timeout=25, verify=False)
    if r.status_code != 200 or b"eyJ" not in r.content:
        print(f"    raw_login[{uid}]: httpx paths failed (status={r.status_code} len={len(r.content)} "
              f"prot={b'Protection' in r.content}) — trying game_register requests path", flush=True)
        # third attempt: game_register-style login via requests through the BD proxy
        # session (this exact flow works for mint, so reuse it for farming too)
        try:
            import game_register as gr
            wire3 = (gr.pf(22, oid) + gr.pf(23, "4") + gr.pf(29, at) + gr.pf(99, "4"))
            h3 = {"User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
                  "Accept-Encoding": "gzip", "X-GA-SV": "1789638359",
                  "Content-Type": "application/x-www-form-urlencoded",
                  "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1", "ReleaseVersion": "OB55"}
            sess = gr.new_session()
            r = sess.post("https://loginbp.ggpolarbear.com/MajorLogin", headers=h3,
                          data=gr.encrypt_payload(wire3), timeout=20, verify=False)
            print(f"    raw_login[{uid}]: gr path status={r.status_code} len={len(r.content)} "
                  f"prot={b'Protection' in r.content}", flush=True)
        except Exception as e:
            print(f"    raw_login[{uid}]: gr path EXC {str(e)[:80]}", flush=True)
    if r.status_code != 200 or b"eyJ" not in r.content:
        return None
    fields = {}
    for f, k, v in api._walk_pb(r.content[64:]):
        fields.setdefault(f, []).append(v)
    jwt = None
    for v in fields.get(8, []):
        if isinstance(v, bytes) and v.startswith(b"eyJ"):
            jwt = v.decode(); break
    if not jwt:
        return None
    url = fields[10][0].decode(errors="replace") if 10 in fields else "https://clientbp.ppmainecoonghj.com"
    return {"jwt": jwt, "key": fields[22][0], "iv": fields[23][0],
            "ts": fields[21][0] if 21 in fields else int(time.time()),
            "url": url, "region": "BD", "uid": uid, "pw": pw,
            "account_id": fields[1][0] if 1 in fields else int(uid)}


def get_login_data(login):
    """GetLoginData -> (whisper_ip, whisper_port, online_ip, online_port)."""
    pair = _grant_direct(login["uid"], login["pw"])
    if not pair:
        return None
    at2, oid2 = pair
    wire = (api._pf_str(22, oid2) + api._pf_str(23, "4")
            + api._pf_str(29, at2) + api._pf_str(99, "4"))
    body = api._enc(wire)
    h = {"User-Agent": api.LOGIN_UA, "Connection": "Keep-Alive",
         "Accept-Encoding": "gzip", "X-GA-SV": str(int(time.time())),
         "Content-Type": "application/x-www-form-urlencoded", "Expect": "100-continue",
         "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1", "ReleaseVersion": "OB55",
         "Authorization": f"Bearer {login['jwt']}"}
    r = httpx.post(login["url"].rstrip("/") + "/GetLoginData", headers=h,
                   data=body, timeout=25, verify=False)
    if r.status_code != 200:
        print(f"    GetLoginData httpx failed (status={r.status_code}) — requests fallback", flush=True)
        try:
            import game_register as gr
            sess = gr.new_session()
            wire2 = (gr.pf(22, oid2) + gr.pf(23, "4") + gr.pf(29, at2) + gr.pf(99, "4"))
            h2 = {"User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
                  "Accept-Encoding": "gzip", "X-GA-SV": "1789638359",
                  "Content-Type": "application/x-www-form-urlencoded",
                  "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1", "ReleaseVersion": "OB55",
                  "Authorization": f"Bearer {login['jwt']}"}
            r = sess.post(login["url"].rstrip("/") + "/GetLoginData", headers=h2,
                          data=gr.encrypt_payload(wire2), timeout=20, verify=False)
            print(f"    GetLoginData requests fallback: status={r.status_code} len={len(r.content)}", flush=True)
        except Exception as e:
            print(f"    GetLoginData requests fallback EXC: {str(e)[:80]}", flush=True)
    if r.status_code != 200:
        return None
    fields = {}
    for f, k, v in api._walk_pb(r.content):
        fields.setdefault(f, []).append(v)
    on = fields.get(14, [None])[0]
    wh = fields.get(32, [None])[0]
    if not on or not wh:
        return None
    def split(addr):
        s = addr.decode(errors="replace")
        ip, port = s.rsplit(":", 1)
        return ip, int(port)
    w_ip, w_port = split(wh)
    o_ip, o_port = split(on)
    return (w_ip, w_port, o_ip, o_port)


async def build_conn_token(login):
    """Token sent raw on both TCP sockets: 0115{hdr}{uid_hex}{ts_hex}00000{len}{enc}."""
    jwt_hex = login["jwt"].encode().hex()
    enc = await EnC_PacKeT(jwt_hex, login["key"], login["iv"])
    uid_hex = hex(login["account_id"])[2:]
    L = len(uid_hex)
    hdr = {9: "0000000", 8: "00000000", 10: "000000", 7: "000000000"}.get(L, "0000000")
    ts_hex = await DecodE_HeX(login["ts"])
    enc_len = hex(len(enc) // 2)[2:]
    return f"0115{hdr}{uid_hex}{ts_hex}00000{enc_len}{enc}"


async def run_account(uid, pw, cycles, on_done):
    tag = f"[{uid}]"
    print(f"{tag} authenticating...", flush=True)
    login = raw_login(uid, pw)
    if not login:
        stage = "unknown"
        try:
            at2, oid2 = api.guest_grant(uid, pw)
            stage = "majorlogin_failed" if at2 else "grant_failed"
        except Exception as e:
            stage = "err:" + str(e)[:40]
        print(f"{tag} LOGIN FAILED ({stage})", flush=True)
        res = {"status": "login_failed", "stage": stage, "cycles": 0}
        on_done(uid, res)
        return res
    servers = get_login_data(login)
    if not servers:
        print(f"{tag} GetLoginData FAILED", flush=True)
        on_done(uid, {"status": "logindata_failed", "cycles": 0})
        return {"status": "logindata_failed", "cycles": 0}
    w_ip, w_port, o_ip, o_port = servers
    token = await build_conn_token(login)
    print(f"{tag} online={o_ip}:{o_port} whisper={w_ip}:{w_port}", flush=True)

    conn = GameConnection()
    conn.set_crypto(login["key"], login["iv"], region="bd")
    pb = PacketBuilder(login["key"], login["iv"], region="bd")
    try:
        await conn.connect(w_ip, w_port, o_ip, o_port, token)
    except Exception as e:
        print(f"{tag} TCP CONNECT FAILED: {e}", flush=True)
        on_done(uid, {"status": "tcp_failed", "err": str(e)[:100], "cycles": 0})
        return {"status": "tcp_failed", "err": str(e)[:100], "cycles": 0}
    print(f"{tag} TCP CONNECTED ✅ starting match loop", flush=True)

    try:
        _cfg = json.loads((ROOT / "level_config.json").read_text()) if (ROOT / "level_config.json").exists() else {}
    except Exception:
        _cfg = {}
    engine = MatchEngine(conn, pb,
                         spam_duration=_cfg.get("spam_duration", 17),
                         spam_delay=_cfg.get("spam_delay", 0.35),
                         wait_after_match=_cfg.get("wait_after_match", 35),
                         uid=login["account_id"])
    try:
        tc = (json.loads((ROOT / "level_config.json").read_text()).get("team_code") or "0")
    except Exception:
        tc = "0"
    stats = await engine.start(tc, max_cycles=cycles)  # "0" = solo, else squad team-code
    try:
        await conn.close()
    except Exception:
        pass
    lvl = profile_level_exp(login["jwt"], login["uid"], login.get("url"))
    res = {"status": "done", "cycles": stats.cycles_completed,
           "level": lvl.get("level"), "exp": lvl.get("exp"), "ts": int(time.time())}
    print(f"{tag} DONE cycles={stats.cycles_completed} level={res['level']} exp={res['exp']}", flush=True)
    # auto-join guild once level >= 6 (guild min requirement)
    try:
        cfg2 = json.loads((ROOT / "level_config.json").read_text()) if (ROOT / "level_config.json").exists() else {}
        clan = cfg2.get("clan_id")
        if clan:
            # guild membership is what turns these matches into GUILD GLORY, so we
            # always try to join and log the server's exact answer.
            ok, msg = api.request_join_clan(int(clan), login["jwt"], verbose=True)
            res["join"] = "OK" if ok else str(msg)[:200]
            print(f"{tag} guild join ({clan}) level={res.get('level')}: {res['join']}", flush=True)
    except Exception as e:
        res["join"] = f"err:{e}"[:60]
    on_done(uid, res)
    return res


def profile_level_exp(jwt, uid, base_url=None):
    """Raw profile fetch: f6=level, f7=exp, f21=likes."""
    try:
        body = api._enc(b"\x08" + api._pb_varint(int(uid)))
        host = (base_url or "https://clientbp.ggpolarbear.com").rstrip("/")
        r = httpx.post(host + "/GetPlayerPersonalShow",
            headers=api._headers(jwt, "OB55"), data=body, timeout=20, verify=False)
        out = {}
        for f, t, v in api._walk_pb(r.content):
            if t == "bytes" and len(v) > 4:
                for sf, st, sv in api._walk_pb(v):
                    if sf in (6, 7, 21) and st == "int":
                        out[sf] = sv
        return {"level": out.get(6), "exp": out.get(7), "likes": out.get(21)}
    except Exception:
        return {}



# ---- external token pool mode (BD JWTs from community pools, no passwords needed) ----

def jwt_claims(jwt):
    p = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def ext_login_data(jwt):
    """GetLoginData via Bearer JWT only -> (w_ip, w_port, o_ip, o_port, f34) or None."""
    try:
        H = dict(api._headers(jwt, "OB55"))
        H["Authorization"] = f"Bearer {jwt}"
        H["Content-Type"] = "application/octet-stream"
        r = httpx.post("https://clientbp.ggpolarbear.com/GetLoginData", headers=H,
                       data=api._enc(b""), timeout=25, verify=False)
        if r.status_code != 200:
            print(f"[extLD] HTTP {r.status_code}: {r.content[:60]}", flush=True)
            return None
        if b"Protection Bypass" in r.content:
            print("[extLD] Protection Bypass flag", flush=True)
            return None
        try:
            fields = {}
            for f, k, v in api._walk_pb(r.content):
                fields.setdefault(f, []).append(v)

            def ipport(b):
                ip, _, port = b.decode(errors="replace").partition(":")
                return ip, int(port or 0)

            o_ip, o_port = ipport(fields[14][0])
            w_ip, w_port = ipport(fields[32][0])
            return (w_ip, w_port, o_ip, o_port, fields.get(34, [b""])[0])
        except Exception as e:
            print(f"[extLD] parse err: {e} | body: {r.content[:60]}", flush=True)
            return None
    except Exception as e:
        print(f"[extLD] req err: {str(e)[:80]}", flush=True)
        return None


async def ext_build_token(account_id, jwt, key, iv, ts):
    jwt_hex = jwt.encode().hex()
    enc = await EnC_PacKeT(jwt_hex, key, iv)
    uid_hex = hex(account_id)[2:]
    L = len(uid_hex)
    hdr = {9: "0000000", 8: "00000000", 10: "000000", 7: "000000000"}.get(L, "0000000")
    ts_hex = await DecodE_HeX(ts)
    enc_len = hex(len(enc) // 2)[2:]
    return f"0115{hdr}{uid_hex}{ts_hex}00000{enc_len}{enc}"


async def run_external(entry, cycles, on_done):
    """Farm one external BD token: try key candidates, run match cycles, auto-join at lvl>=6."""
    jwt = entry["token"]
    c = jwt_claims(jwt)
    account_id = c.get("account_id")
    uid = str(c.get("external_uid") or c.get("account_id"))
    tag = f"[ext:{uid}]"
    if not account_id:
        print(f"{tag} no account_id", flush=True)
        return {"status": "ext_no_account", "cycles": 0}
    servers = ext_login_data(jwt)
    if not servers:
        print(f"{tag} GetLoginData failed", flush=True)
        return {"status": "ext_logindata_failed", "cycles": 0}
    w_ip, w_port, o_ip, o_port, f34 = servers
    print(f"{tag} online={o_ip}:{o_port} whisper={w_ip}:{w_port} f34len={len(f34)} f34={f34.hex()}", flush=True)

    cands = [("default", api.AES_KEY, api.AES_IV)]
    if len(f34) >= 32:
        cands.append(("f34halves", f34[:16], f34[16:32]))
    cands.append(("bd_static", bytes.fromhex("71ca1a47fef365f7504205380a321025"),
                  bytes.fromhex("40f26949fef476f37102055014310026")))
    for tagk, key, iv in cands:
        token = await ext_build_token(account_id, jwt, key, iv, int(time.time()))
        conn = GameConnection()
        conn.set_crypto(key, iv, region="bd")
        pb = PacketBuilder(key, iv, region="bd")
        try:
            await asyncio.wait_for(conn.connect(w_ip, w_port, o_ip, o_port, token), timeout=25)
        except Exception as e:
            print(f"{tag} TCP {tagk} failed: {str(e)[:70]}", flush=True)
            continue
        print(f"{tag} TCP CONNECTED with key={tagk}", flush=True)

        # handshake check: listen 10s for any server response
        rx_count = 0
        for _ in range(5):
            d1 = await conn.recv_online(1.5)
            if d1:
                rx_count += 1
                print(f"{tag} online RX: {d1.hex()[:120]}", flush=True)
            d2 = await conn.recv_whisper(0.5)
            if d2:
                rx_count += 1
                print(f"{tag} chat RX: {d2.hex()[:120]}", flush=True)
        alive = (conn.online_writer and not conn.online_writer.is_closing()
                 and not conn.online_reader.at_eof())
        print(f"{tag} handshake: rx={rx_count} online_alive={alive} connected={conn.connected}", flush=True)
        if rx_count == 0:
            print(f"{tag} no server RX with key={tagk} — key likely wrong, trying next", flush=True)
            try:
                await conn.close()
            except Exception:
                pass
            continue
        engine = MatchEngine(conn, pb, spam_duration=17, spam_delay=0.2,
                             wait_after_match=20, uid=account_id)
        stats = await engine.start("0", max_cycles=cycles)
        try:
            await conn.close()
        except Exception:
            pass
        prof = profile_level_exp(jwt, uid) if jwt else {}
        dead = not (conn.online_writer and not conn.online_writer.is_closing())
        res = {"status": "done" if not dead and stats.cycles_completed > 0 else "tcp_dead",
               "mode": "external", "key": tagk,
               "cycles": stats.cycles_completed, "level": prof.get("level"),
               "exp": prof.get("exp"), "ts": int(time.time())}
        print(f"{tag} DONE cycles={res['cycles']} level={res['level']} exp={res['exp']} key={tagk}", flush=True)
        if prof.get("level") and prof["level"] >= 6:
            try:
                try:
                    cfg3 = json.loads((ROOT / "level_config.json").read_text())
                except Exception:
                    cfg3 = {}
                ok, msg = api.request_join_clan(cfg3.get("clan_id", 0) or 3071565979, jwt)
                res["join"] = msg
                print(f"{tag} RequestJoinClan: {ok} {msg}", flush=True)
            except Exception as e:
                res["join"] = f"ERR:{e}"
        on_done(uid, res)
        return res
    res = {"status": "ext_tcp_all_keys_failed", "cycles": 0, "ts": int(time.time())}
    on_done(uid, res)
    return res


def last_attempt(uid):
    e = (load_progress().get(uid) or {})
    return e.get("ts", 0)


COOLDOWN_S = 2 * 3600  # per-account retry cooldown (avoid re-flagging)


def load_progress():
    try:
        return json.loads(PROGRESS_FILE.read_text())
    except Exception:
        return {}


def save_progress(uid, res):
    d = load_progress()
    d[uid] = res
    PROGRESS_FILE.write_text(json.dumps(d, indent=1))
    _push_progress()


def _push_progress():
    """Commit progress immediately so the bot shows live active/inactive counts."""
    tok = os.environ.get("GITHUB_USAGE_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo or not PROGRESS_FILE.exists():
        return
    api = f"https://api.github.com/repos/{repo}/contents/level_progress.json"
    for attempt in range(3):  # retry on sha race between parallel workers
        try:
            r = httpx.get(api, headers={"Authorization": f"Bearer {tok}"}, timeout=15)
            sha = r.json().get("sha") if r.status_code == 200 else None
            resp = httpx.put(api, headers={"Authorization": f"Bearer {tok}"}, timeout=15,
                             json={"message": "engine activation progress",
                                   "content": base64.b64encode(PROGRESS_FILE.read_bytes()).decode(),
                                   "sha": sha})
            if resp.status_code in (200, 201):
                return
        except Exception as e:
            print("progress push failed:", e)
            return
        time.sleep(1)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", type=int, default=0, help="first N accounts")
    ap.add_argument("--uid", type=str, default="")
    ap.add_argument("--cycles", type=int, default=60)
    args = ap.parse_args()

    cfg = {}
    try:
        cfg = json.loads(Path("level_config.json").read_text())
    except Exception:
        pass
    wh_report({"engine": "started", "pid": os.getpid(), "cfg": cfg, "ts": time.time()})
    _heartbeat_thread()
    prefer = cfg.get("prefer") or []
    want = cfg.get("accounts") or args.all or 4
    cycles = cfg.get("cycles") or args.cycles
    # AUTO ACTIVATION: phone-born accounts never played a match go first
    if cfg.get("auto_phone"):
        try:
            pa = json.loads((Path(__file__).parent.parent / "phone_accounts.json").read_text())
            prog = load_progress()
            new_phone = [str(r.get("uid")) for r in pa if str(r.get("uid")) not in prog]
            prefer = new_phone + [u for u in prefer if u not in new_phone]
            print(f"auto_phone: {len(new_phone)} new phone accounts to activate", flush=True)
        except Exception:
            pass

    fresh = []
    if cfg.get("auto_mint") and not args.uid:
        try:
            sys.path.insert(0, str(ROOT / "guests_manager"))
            import game_register as gr
            proxies = []
            if os.environ.get("BD_TUNNEL") == "1":
                print("auto_mint: BD tunnel up — minting directly through tunnel", flush=True)
            else:
                env_px = (os.environ.get("BD_PROXY_URL") or "").strip()
                cached = _load_proxy_cache()
                proxies = []
                if os.environ.get("PHONE_OK") == "1" and env_px:
                    # OWNER'S PHONE PROXY (Tailscale) is live — mint straight through it.
                    proxies = [env_px]
                    print(f"auto_mint: using phone BD proxy {env_px} — hunt skipped", flush=True)
                else:
                    if env_px and not env_px.startswith("socks4"):
                        cached = [env_px] + [u for u in cached if u != env_px]
                    for round_n in range(1, 7):
                        proxies = hunt_bd_proxies(gr, extra=cached)
                        if proxies:
                            break
                        print(f"auto_mint: no usable BD proxy (round {round_n}/6) — retrying in 75s", flush=True)
                        time.sleep(75)
                if proxies:
                    _save_proxy_cache(proxies)
                    print(f"auto_mint: {len(proxies)} usable BD proxies: {proxies}", flush=True)
                else:
                    print("auto_mint: NO usable BD proxy after 6 rounds — cannot mint BD accounts", flush=True)
            fresh = mint_fresh_accounts(int(cfg["auto_mint"]), gr, proxies)
            print(f"auto_mint: {len(fresh)} fresh BD accounts minted", flush=True)
            wh_report({"engine": "mint_done", "minted": len(fresh), "ts": time.time()})
        except Exception as e:
            print(f"auto_mint setup EXC: {str(e)[:120]}", flush=True)

    if fresh:
        save_fresh_to_pool(fresh)
    if cfg.get("farm") is False:
        print("mint-only mode (farm:false): match engine SKIPPED — minted accounts saved to the like pool", flush=True)
        wh_report({"engine": "mint_only_done", "minted": len(fresh), "ts": time.time()})
        return

    if args.uid:
        accounts = [(args.uid, os.environ.get("GUEST_PW", ""))]
        want, cycles = 1, args.cycles
    else:
        accounts = load_accounts(max(want * 3, 8), prefer=prefer)
        if fresh:
            accounts = fresh + accounts
    print(f"candidates: {len(accounts)} accounts, want={want}, cycles={cycles}, first={[a[0] for a in accounts[:8]]}", flush=True)

    done = 0
    workers = int(cfg.get("workers") or 1)
    if workers > 1 and not args.uid:
        # ==== BULK PARALLEL ACTIVATION ====
        sem = asyncio.Semaphore(workers)
        counter = [0]

        async def _worker(uid, pw):
            if counter[0] >= want:
                return
            if time.time() - last_attempt(uid) < COOLDOWN_S:
                print(f"[{uid}] cooldown skip", flush=True)
                return
            async with sem:
                if counter[0] >= want:
                    return
                wh_report({"uid": uid, "phase": "attempt_start", "ts": time.time()})
                try:
                    res = await asyncio.wait_for(run_account(uid, pw, cycles, save_progress), timeout=420)
                except asyncio.TimeoutError:
                    res = {"status": "watchdog_timeout", "cycles": 0}
                wh_report({"uid": uid, "result": res, "ts": time.time()})
                if res and res.get("status") == "done":
                    counter[0] += 1
                print(f"[{uid}] -> {res.get('status') if res else '?'} | done={counter[0]}", flush=True)

        print(f"BULK MODE: {len(accounts)} candidates, {workers} parallel workers, cycles={cycles}", flush=True)
        await asyncio.gather(*[_worker(u, p) for u, p in accounts])
        done = counter[0]
    else:
        for uid, pw in accounts:
            if not args.uid and time.time() - last_attempt(uid) < COOLDOWN_S:
                print(f"[{uid}] cooldown skip (attempted < 2h ago)", flush=True)
                continue
            wh_report({"uid": uid, "phase": "attempt_start", "ts": time.time()})
            try:
                res = await asyncio.wait_for(run_account(uid, pw, cycles, save_progress), timeout=1500)
            except asyncio.TimeoutError:
                res = {"status": "watchdog_timeout", "cycles": 0}
            wh_report({"uid": uid, "result": res, "ts": time.time()})
            await asyncio.sleep(3)
            if res and res.get("status") == "done":
                done += 1
            if done >= want:
                break

    # external pool fallback: farm BD JWTs from community token pools
    if done < want and cfg.get("external", True):
        try:
            all_ext = [t for t in api.fetch_external_tokens() if t.get("region") == "BD"]
        except Exception:
            all_ext = []
        pref = cfg.get("prefer") or []
        if pref:
            exts = [t for t in all_ext if t.get("uid") in pref][:6] or all_ext[:6]
        else:
            exts = all_ext[:6]
        print(f"external fallback: {len(exts)} BD tokens", flush=True)
        for e in exts:
            res = await run_external(e, cycles, save_progress)
            wh_report({"uid": "ext:" + str(e.get("uid", "?")), "result": res, "ts": time.time()})
            if res and res.get("status") == "done":
                done += 1
                if done >= want:
                    break
            await asyncio.sleep(3)

    d = load_progress()
    print("PROGRESS:", json.dumps(d)[-1500:], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
