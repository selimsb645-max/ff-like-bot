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
import ff_bd_api as api  # our proven HTTP auth/pool module

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
    if prefer:
        pref = [(u, pw) for u, pw in out if u in prefer]
        rest = [(u, pw) for u, pw in out if u not in prefer]
        out = pref + rest
    return out[:limit]


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


def _full_device_wire(oid, at, lang="bn"):
    import uuid as _uuid
    model, os_s, gpu = random.choice(DEVICES)
    fields = {
        3: datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        4: "free fire", 5: 4, 7: "1.132.1", 8: "2019116753",
        9: os_s, 10: "Handheld", 11: model, 12: 1280, 13: 720, 14: "240",
        15: "x86-64 SSE3 SSE4.1 SSE4.2 AVX AVX2 | 2400 | 4",
        16: random.choice([5951, 6000, 6100, 5500]), 17: gpu,
        18: "OpenGL ES 3.1 v1.46", 19: f"Google|{_uuid.uuid4()}",
        20: random.choice(BD_IPS), 21: lang, 22: oid, 23: "4", 24: "Handheld",
        25: model, 26: "BD", 29: at, 30: 1, 41: "Grameenphone", 42: "WIFI",
        92: random.choice([19788, 20000, 21000]), 93: "android_max",
        97: 1, 98: 1, 99: "4", 100: "4", 104: 77149, 105: 1,
    }
    return b"".join(_pf_full(k, v) for k, v in fields.items())


def raw_login(uid, pw):
    """guest grant + raw MajorLogin -> dict(jwt, key, iv, ts, url, region, account_id) or None."""
    at, oid = api.guest_grant(uid, pw)
    if not at:
        return None
    wire = _full_device_wire(oid, at)
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
    pair = api.guest_grant(login["uid"], login["pw"])
    if not pair:
        return None
    at2, oid2 = pair
    wire = (api._pf_str(22, oid2) + api._pf_str(23, "4")
            + api._pf_str(29, at2) + api._pf_str(99, "4"))
    body = api._enc(wire)
    h = {"User-Agent": api.LOGIN_UA, "Connection": "Keep-Alive",
         "Accept-Encoding": "gzip", "X-GA-SV": "1789638359",
         "Content-Type": "application/x-www-form-urlencoded", "Expect": "100-continue",
         "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1", "ReleaseVersion": "OB55",
         "Authorization": f"Bearer {login['jwt']}"}
    r = httpx.post(login["url"].rstrip("/") + "/GetLoginData", headers=h,
                   data=body, timeout=25, verify=False)
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
        print(f"{tag} LOGIN FAILED", flush=True)
        on_done(uid, {"status": "login_failed", "cycles": 0})
        return {"status": "login_failed", "cycles": 0}
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

    engine = MatchEngine(conn, pb, spam_duration=17, spam_delay=0.2,
                         wait_after_match=20, uid=login["account_id"])
    stats = await engine.start("0", max_cycles=cycles)  # "0" = Lone Wolf solo
    try:
        await conn.close()
    except Exception:
        pass
    lvl = profile_level_exp(login["jwt"], login["uid"])
    res = {"status": "done", "cycles": stats.cycles_completed,
           "level": lvl.get("level"), "exp": lvl.get("exp"), "ts": int(time.time())}
    print(f"{tag} DONE cycles={stats.cycles_completed} level={res['level']} exp={res['exp']}", flush=True)
    # auto-join guild once level >= 6 (guild min requirement)
    try:
        cfg2 = json.loads((ROOT / "level_config.json").read_text()) if (ROOT / "level_config.json").exists() else {}
        clan = cfg2.get("clan_id")
        if clan and (res.get("level") or 0) >= 6:
            ok, msg = api.request_join_clan(int(clan), login["jwt"])
            res["join"] = f"{'OK' if ok else msg}"
            print(f"{tag} guild join ({clan}): {res['join']}", flush=True)
    except Exception as e:
        res["join"] = f"err:{e}"[:60]
    on_done(uid, res)
    return res


def profile_level_exp(jwt, uid):
    """Raw profile fetch: f6=level, f7=exp, f21=likes."""
    try:
        body = api._enc(b"\x08" + api._pb_varint(int(uid)))
        r = httpx.post("https://clientbp.ggpolarbear.com/GetPlayerPersonalShow",
            headers=api._headers(login["jwt"], "OB55"), data=body, timeout=20, verify=False)
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
    print(f"{tag} online={o_ip}:{o_port} whisper={w_ip}:{w_port} f34={f34.hex()[:20]}", flush=True)

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
        engine = MatchEngine(conn, pb, spam_duration=17, spam_delay=0.2,
                             wait_after_match=20, uid=account_id)
        stats = await engine.start("0", max_cycles=cycles)
        try:
            await conn.close()
        except Exception:
            pass
        prof = profile_level_exp(jwt, uid) if jwt else {}
        res = {"status": "done", "mode": "external", "key": tagk,
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
    prefer = cfg.get("prefer") or []
    want = cfg.get("accounts") or args.all or 4
    cycles = cfg.get("cycles") or args.cycles

    if args.uid:
        accounts = [(args.uid, os.environ.get("GUEST_PW", ""))]
        want, cycles = 1, args.cycles
    else:
        accounts = load_accounts(max(want * 3, 8), prefer=prefer)
    print(f"candidates: {[a[0] for a in accounts]}, want={want}, cycles={cycles}", flush=True)

    done = 0
    for uid, pw in accounts:
        if not args.uid and time.time() - last_attempt(uid) < COOLDOWN_S:
            print(f"[{uid}] cooldown skip (attempted < 2h ago)", flush=True)
            continue
        res = await run_account(uid, pw, cycles, save_progress)
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
