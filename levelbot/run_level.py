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


def raw_login(uid, pw):
    """guest grant + raw MajorLogin -> dict(jwt, key, iv, ts, url, region, account_id) or None."""
    at, oid = api.guest_grant(uid, pw)
    if not at:
        return None
    wire = (api._pf_str(22, oid) + api._pf_str(23, "4")
            + api._pf_str(29, at) + api._pf_str(99, "4"))
    body = api._enc(wire)
    h = {"User-Agent": api.LOGIN_UA, "Connection": "Keep-Alive",
         "Accept-Encoding": "gzip", "X-GA-SV": "1789638359",
         "Content-Type": "application/x-www-form-urlencoded", "Expect": "100-continue",
         "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1", "ReleaseVersion": "OB55"}
    r = httpx.post(ML_URL, headers=h, data=body, timeout=25, verify=False)
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
    lvl = profile_level_exp(login)
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


def profile_level_exp(login):
    """Raw profile fetch: f6=level, f7=exp, f21=likes."""
    try:
        body = api._enc(b"\x08" + api._pb_varint(int(login["uid"])))
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
        res = await run_account(uid, pw, cycles, save_progress)
        wh_report({"uid": uid, "result": res, "ts": time.time()})
        await asyncio.sleep(3)
        if res and res.get("status") == "done":
            done += 1
        if done >= want:
            break

    d = load_progress()
    print("PROGRESS:", json.dumps(d)[-1500:], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
