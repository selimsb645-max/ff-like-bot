#!/usr/bin/env python3
"""Turn a registered guest into a *usable* account: logged in, in-game, profiled.

Creating an account is the easy half. An account is only worth anything when the game
has given it a profile, because LikeProfile answers 200 either way and silently counts
nothing for an account the game does not know. Activation therefore has a hard check at
the end: get_stats must return a name. Until it does, the account stays pending.

    python3 activate.py --report
    python3 activate.py --pending 8       # work through unprofiled accounts
    python3 activate.py --uid 8405933510
    python3 activate.py --create 3        # register, then activate, in one go

Run it with the phone tunnel up:  ./tunnel_up.sh
"""
import argparse
import asyncio
import json
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

import ff_bd_api as api
import supply
from guests_manager import game_register as gr
from full_login import full_major_login
from levelbot.run_level import build_conn_token, get_login_data
from levelbot.connection import GameConnection

POOL = supply.OWNED
TOKEN_LIFE = 6 * 3600


def load():
    return json.loads(POOL.read_text())


def save(pool):
    tmp = POOL.with_suffix(".tmp")
    tmp.write_text(json.dumps(pool, indent=1))
    tmp.replace(POOL)


def crypto(rec):
    return (bytes.fromhex(rec["key"]) if isinstance(rec["key"], str) else rec["key"],
            bytes.fromhex(rec["iv"]) if isinstance(rec["iv"], str) else rec["iv"])


def profile_of(rec):
    """Return the in-game profile dict, or None when the game has no such account."""
    st = api.get_stats(int(rec["uid"]), rec["token"])
    if st and st.get("name"):
        return st
    return None


def relogin(rec):
    """Fresh JWT for an account we own. Two passes: the second yields the long token."""
    sess = gr.new_session()
    at, oid = gr.token_grant(sess, rec["uid"], rec["pass"])
    if not at:
        return False
    info = full_major_login(rec["uid"], oid, at)
    if not info:
        return False
    for _ in range(2):
        again = full_major_login(rec["uid"], oid, at)
        if again and len(again.get("jwt", "")) > len(info.get("jwt", "")):
            info = again
        if len(info.get("jwt", "")) >= 815:
            break
    rec.update({"token": info["jwt"], "account_id": info.get("account_id") or rec.get("account_id"),
                "server_url": info.get("url") or rec.get("server_url"),
                "key": info["key"].hex() if isinstance(info.get("key"), (bytes, bytearray)) else info.get("key"),
                "iv": info["iv"].hex() if isinstance(info.get("iv"), (bytes, bytearray)) else info.get("iv"),
                "ts": info.get("ts"), "exp": time.time() + TOKEN_LIFE, "last_ok": time.time()})
    return True


def enter_game(rec, hold=20):
    """GetLoginData + TCP presence. Returns True when endpoints were obtained."""
    key, iv = crypto(rec)
    login = {"uid": rec["uid"], "pw": rec["pass"], "jwt": rec["token"], "key": key, "iv": iv,
             "account_id": rec.get("account_id") or int(rec["uid"]),
             "ts": rec.get("ts") or int(time.time()), "region": "BD",
             "url": rec.get("server_url")}
    data = get_login_data(login)
    if not data:
        return False
    rec["endpoints"] = list(data)
    login.update({"whisper_ip": data[0], "whisper_port": data[1],
                  "online_ip": data[2], "online_port": data[3]})

    async def go():
        c = GameConnection()
        c.set_crypto(key, iv, "BD")
        try:
            await c.connect(data[0], data[1], data[2], data[3],
                            await build_conn_token(login), await build_conn_token(login, chat=True))
            await asyncio.sleep(hold)
        except Exception:
            pass
        finally:
            try:
                await c.close()
            except Exception:
                pass

    asyncio.run(go())
    return True


def activate(rec, tries=3):
    """Attempt to get this account a profile. Records every attempt on the record."""
    rec.setdefault("activation_attempts", 0)
    for attempt in range(1, tries + 1):
        rec["activation_attempts"] += 1
        rec["last_activation"] = time.time()
        if attempt >= 2 and not (rec.get("exp") or 0) > time.time() + 300:
            if not relogin(rec):
                # A guest login that the server refuses twice is gone for good; keep it
                # out of the queue instead of burning minutes on it every run.
                rec["dead"] = True
                rec["profiled"] = False
                return False
        if not enter_game(rec):
            time.sleep(10)
            continue
        prof = profile_of(rec)
        if prof:
            rec["profiled"] = True
            rec["profile_name"] = prof.get("name")
            rec["profile_likes"] = prof.get("likes")
            return True
        time.sleep(15)
    rec["profiled"] = False
    return False


def report(pool):
    now = time.time()
    live = [r for r in pool if (r.get("exp") or 0) > now]
    profiled = [r for r in pool if r.get("profiled")]
    pending = [r for r in pool if not r.get("profiled") and not r.get("dead")]
    dead = [r for r in pool if r.get("dead")]
    attempted = [r for r in pool if r.get("activation_attempts")]
    print(f"accounts {len(pool)} | live token {len(live)} | profiled {len(profiled)} | "
          f"pending {len(pending)} | dead {len(dead)} | ever attempted {len(attempted)}")
    if attempted:
        won = len([r for r in attempted if r.get("profiled")])
        print(f"activation success rate: {won}/{len(attempted)}")
    for r in profiled[:5]:
        print(f"  ✓ {r['uid']} {r.get('profile_name')} likes={r.get('profile_likes')}")
    for r in pending[:5]:
        print(f"  · {r['uid']} attempts={r.get('activation_attempts', 0)} "
              f"token={'live' if (r.get('exp') or 0) > now else 'expired'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--pending", type=int, default=0)
    ap.add_argument("--uid")
    ap.add_argument("--create", type=int, default=0)
    a = ap.parse_args()

    pool = load()

    if a.create:
        sess = gr.new_session()
        made = 0
        while made < a.create:
            rec = supply.create_one(sess, tries=1)
            if not rec:
                print("registration throttled — waiting 120s", flush=True)
                time.sleep(120)
                continue
            pool.append(rec)
            save(pool)
            made += 1
            print(f"created {rec['uid']} {rec['nick']} token={len(rec['token'])}", flush=True)
            time.sleep(30)
        print(f"created {made}; now activating", flush=True)

    targets = []
    if a.uid:
        targets = [r for r in pool if str(r["uid"]) == str(a.uid)]
    elif a.pending:
        fresh = [r for r in pool if not r.get("profiled") and not r.get("dead")]
        fresh.sort(key=lambda r: r.get("created") or 0, reverse=True)
        targets = fresh[:a.pending]

    done = 0
    for rec in targets:
        ok = activate(rec)
        done += bool(ok)
        save(pool)
        print(f"{'✓' if ok else '✗'} {rec['uid']} profiled={rec.get('profiled')} "
              f"attempts={rec.get('activation_attempts')}", flush=True)
        time.sleep(5)

    if targets:
        print(f"activated {done}/{len(targets)}", flush=True)
    report(load())


if __name__ == "__main__":
    main()
