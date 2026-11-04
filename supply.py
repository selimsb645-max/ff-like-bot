#!/usr/bin/env python3
"""Owned account supply for the like bot — create, enter game, renew, prune.

Why this exists: the public pools are shared, so the same token is burned by other
bots and the daily capacity collapses after midday. Capacity is only real when the
accounts are ours: we create them from the BD exit, we put each one into the game
once (a like only counts from an account with an in-game profile), and we renew the
JWT from our own stored device fingerprint instead of racing a public feed.

    python3 supply.py --status                 # what we have, what is dying
    python3 supply.py --ensure 250             # renew + create up to 250 owned
    python3 supply.py --ensure 250 --create-max 40
    python3 supply.py --presence 20            # enter game for accounts lacking a profile
    python3 supply.py --prune                  # drop tokens the game rejects

Every network call must leave through the owner's BD phone, so run it with
BD_PROXY_URL pointing at the phone tunnel (socks5://127.0.0.1:1090 or
http://127.0.0.1:8899 through chain_proxy.py).
"""
import argparse
import asyncio
import json
import random
import string
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import ff_bd_api as api
from guests_manager import game_register as gr
from full_login import full_major_login

OWNED = ROOT / "bd_pool_owned.json"
TOKEN_LIFE = 6 * 3600          # observed: a BD guest JWT is good for ~6-8h
RENEW_BEFORE = 90 * 60         # renew this long before expiry
PRESENCE_MAX = 12              # entering the game is heavy; cap per run


def load() -> list:
    try:
        return json.loads(OWNED.read_text())
    except Exception:
        return []


def save(pool: list) -> None:
    tmp = OWNED.with_suffix(".tmp")
    tmp.write_text(json.dumps(pool, indent=1))
    tmp.replace(OWNED)


def alive(rec: dict) -> bool:
    return (rec.get("exp") or 0) > time.time() + 60


def record(uid, pw, info, nick=None, profiled=None):
    return {
        "uid": str(uid), "pass": pw, "owned": True,
        "account_id": info.get("account_id"), "token": info["jwt"],
        "key": info["key"].hex() if isinstance(info.get("key"), (bytes, bytearray)) else info.get("key"),
        "iv": info["iv"].hex() if isinstance(info.get("iv"), (bytes, bytearray)) else info.get("iv"),
        "server_url": info.get("url"), "region": info.get("region") or "BD",
        "nick": nick, "exp": time.time() + TOKEN_LIFE,
        "created": time.time(), "profiled": profiled, "last_ok": time.time(),
    }


def renew(rec: dict, sess) -> bool:
    """Re-login an account we already own, using its own stored credentials."""
    try:
        at, oid = gr.token_grant(sess, rec["uid"], rec["pass"])
        if not at:
            return False
        info = full_major_login(rec["uid"], oid, at)
        if not info:
            return False
        rec.update({"token": info["jwt"], "account_id": info.get("account_id") or rec.get("account_id"),
                    "server_url": info.get("url") or rec.get("server_url"),
                    "key": info["key"].hex() if isinstance(info.get("key"), (bytes, bytearray)) else info.get("key"),
                    "iv": info["iv"].hex() if isinstance(info.get("iv"), (bytes, bytearray)) else info.get("iv"),
                    "exp": time.time() + TOKEN_LIFE, "last_ok": time.time()})
        return True
    except Exception:
        return False


async def enter_game(rec: dict) -> bool:
    """GetLoginData + TCP presence: this is what gives the account a game profile."""
    from levelbot.connection import GameConnection
    from levelbot.run_level import build_conn_token, get_login_data
    key = bytes.fromhex(rec["key"]) if isinstance(rec["key"], str) else rec["key"]
    iv = bytes.fromhex(rec["iv"]) if isinstance(rec["iv"], str) else rec["iv"]
    login = {"uid": rec["uid"], "pw": rec["pass"], "jwt": rec["token"], "key": key, "iv": iv,
             "account_id": rec.get("account_id") or int(rec["uid"]), "ts": int(time.time()),
             "region": "BD", "url": rec.get("server_url")}
    data = get_login_data(login)
    if not data:
        return False
    login.update({"whisper_ip": data[0], "whisper_port": data[1],
                  "online_ip": data[2], "online_port": data[3]})
    conn = GameConnection()
    conn.set_crypto(key, iv, "BD")
    try:
        await conn.connect(login["whisper_ip"], login["whisper_port"],
                           login["online_ip"], login["online_port"],
                           await build_conn_token(login),
                           await build_conn_token(login, chat=True))
        await asyncio.sleep(18)
        return True
    except Exception:
        return False
    finally:
        try:
            await conn.close()
        except Exception:
            pass


def create_one(sess, tries=3):
    """Register a brand new BD guest and log it in with the full-device payload."""
    for _ in range(tries):
        cred, why = gr.msdk_register(sess)
        if not cred:
            print("  register:", why, flush=True)
            continue
        uid, pw = cred
        at, oid = gr.token_grant(sess, uid, pw)
        if not at:
            continue
        nick = "ANON\u2022" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
        ok, msg = gr.major_register(sess, at, oid, nick)
        if not ok:
            print(f"  {uid} major_register: {msg}", flush=True)
            continue
        info = full_major_login(uid, oid, at)
        if not info:
            continue
        return record(uid, pw, info, nick=nick, profiled=False)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--ensure", type=int, default=0)
    ap.add_argument("--create-max", type=int, default=40)
    ap.add_argument("--presence", type=int, default=0)
    ap.add_argument("--prune", action="store_true")
    a = ap.parse_args()

    pool = load()
    now = time.time()
    live = [r for r in pool if alive(r)]
    dying = [r for r in live if r["exp"] < now + RENEW_BEFORE]

    if a.status or not any([a.ensure, a.presence, a.prune]):
        prof = sum(1 for r in live if r.get("profiled"))
        print(f"owned={len(pool)} live={len(live)} profiled={prof} "
              f"expiring<{RENEW_BEFORE//60}m={len(dying)} "
              f"expired={len(pool) - len(live)}")
        if live:
            print("oldest token age (h):", round((now - min(r.get("created", now) for r in live)) / 3600, 1))
        return

    if a.prune:
        keep = []
        dropped = 0
        for r in pool:
            if alive(r) and not api.check_token(r["token"]):
                dropped += 1
                continue
            keep.append(r)
        save(keep)
        print(f"pruned {dropped}, kept {len(keep)}")

    if a.ensure:
        sess = gr.new_session()
        renewed = failed = 0
        for r in dying:
            if renew(r, sess):
                renewed += 1
            else:
                failed += 1
            time.sleep(1)
        save(pool)
        print(f"renewed {renewed}, refused {failed}", flush=True)

        live = [r for r in pool if alive(r)]
        need = min(max(0, a.ensure - len(live)), a.create_max)
        print(f"live={len(live)} need={a.ensure} creating={need}", flush=True)
        made = 0
        for i in range(need):
            rec = create_one(sess)
            if not rec:
                print("create failed — stopping (proxy or throttle)", flush=True)
                break
            ok = asyncio.run(enter_game(rec)) if made < PRESENCE_MAX else False
            rec["profiled"] = bool(ok) or None
            pool.append(rec)
            save(pool)
            made += 1
            print(f"  [{made}/{need}] {rec['uid']} {rec['nick']} token={len(rec['token'])} "
                  f"presence={'ok' if ok else 'no'}", flush=True)
            time.sleep(3)
        print(f"created {made}; pool now {len(pool)} owned")


if __name__ == "__main__":
    main()
