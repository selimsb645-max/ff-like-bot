#!/usr/bin/env python3
"""
GUESTGEN — our own BD guest-account factory.

Why this exists
---------------
The third-party generator (DARK MASTER / GUEST_GEN_FIXED.py) is patched, phones
home to a Telegram channel and dies on a datacenter IP. This tool is ours: it
asks the BD login service for the live version data, then runs the full real
chain - register -> grant -> MajorRegister -> full-device MajorLogin (twice, so
the 819-char game JWT is obtained) -> optional GetLoginData presence, which is
what gives the account an in-game profile.

It does not re-implement any protocol: registration lives in
guests_manager/game_register.py, the device login in full_login.py and the
profile step in supply.py. Those modules are used as they are, so a fix there
reaches this tool too.

The one rule that matters: a guest account's REGION is stamped from the IP it
logs in from. Run this through the phone (BD residential), never from a
datacenter, or you get an account that can never like on the BD server.

Usage
-----
  # live version data only, no account is created
  python3 guestgen.py --probe

  # create 5 accounts through the phone tunnel, then give them profiles
  BD_PROXY_URL=socks5h://127.0.0.1:8899 python3 guestgen.py --count 5 --profile

  # create without the presence step (faster, profile stays empty)
  BD_PROXY_URL=socks5h://127.0.0.1:8899 python3 guestgen.py --count 10

Exit codes: 0 ok, 2 not a BD exit, 3 registration throttled out, 4 nothing minted.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import string
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

POOL = ROOT / "bd_pool.json"
VER_CFG = ROOT / ".guestgen_version.json"
VER_TTL = 20 * 60

# fallback only: the live answer wins, this keeps us working if the endpoint blips
FALLBACK = {"release": "OB55", "login_host": "loginbp.ppmainecoonghj.com", "region": "BD"}
VER_URL = ("https://version.ggwhitehawk.com/live/ver.php?version={ver}&lang=hi"
           "&device=android&channel=android&appstore=googleplay&region=BD"
           "&whitelist_version=1.3.0&whitelist_sp_version=1.0.0")


def log(msg: str) -> None:
    print(msg, flush=True)


def version_data(client_version: str = "1.132.1", force: bool = False) -> dict:
    """Live BD version config: release version + login host.

    Cached for 20 minutes because the endpoint is flaky and every mint needs it.
    A failure falls back to the last good answer, then to the constants above -
    it must never stop account creation by itself.
    """
    if not force and VER_CFG.exists():
        try:
            cached = json.loads(VER_CFG.read_text())
            if time.time() - cached.get("at", 0) < VER_TTL:
                return cached
        except Exception:
            pass

    import urllib.request
    try:
        req = urllib.request.Request(VER_URL.format(ver=client_version),
                                     headers={"User-Agent": "UnityPlayer/2021.3.5f1"})
        raw = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
        host = str(raw.get("server_url", "")).split("//")[-1].split("/")[0]
        data = {
            "at": time.time(),
            "release": raw.get("latest_release_version") or FALLBACK["release"],
            "login_host": host or FALLBACK["login_host"],
            "region": "BD",
            "server_open": bool(raw.get("is_server_open", 1)),
            "remote_version": raw.get("remote_version"),
        }
    except Exception as exc:
        log(f"  [vercfg] live fetch failed ({str(exc)[:60]}), using fallback")
        data = {"at": time.time(), **FALLBACK, "server_open": True, "remote_version": None}

    try:
        VER_CFG.write_text(json.dumps(data, indent=2))
    except Exception:
        pass
    return data


def make_session(proxy: str | None):
    """One requests session for the whole run, carrying the BD proxy."""
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "UnityPlayer/2021.3.5f1 (UnityWebRequest/1.0)"})
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    elif os.environ.get("BD_PROXY_URL"):
        p = os.environ["BD_PROXY_URL"]
        s.proxies = {"http": p, "https": p}
    return s


def exit_ip(sess) -> str:
    try:
        return sess.get("https://api.ipify.org", timeout=20).text.strip()
    except Exception:
        return ""


def is_bd_ip(ip: str) -> bool:
    """The whole tool is worthless from a non-BD IP, so this is a hard gate.

    Lookup is HTTPS-only (plain http is blocked in some sandboxes) and fails
    CLOSED: when the country cannot be established we refuse rather than risk
    stamping an account with the wrong region, which cannot be undone later.
    """
    if not ip:
        return False
    import requests

    country, isp, hosting = None, "", None
    for url, pick in (
        (f"https://api.country.is/{ip}", lambda d: (d.get("country"), d.get("isp", ""), None)),
        (f"https://ipwho.is/{ip}", lambda d: (d.get("country_code"), (d.get("connection") or {}).get("isp", ""), None)),
    ):
        try:
            d = requests.get(url, timeout=15).json()
            c, i, h = pick(d)
            if c:
                country, isp, hosting = c, i or "", h
                break
        except Exception:
            continue

    if not country:
        log(f"  [gate] could not classify {ip} - refusing to mint (set --no-ip-gate only for tests)")
        return False
    if country != "BD":
        log(f"  [gate] exit {ip} is {country}, not BD - refusing to mint")
        return False
    if hosting:
        log(f"  [gate] exit {ip} is a datacenter ({isp}) - refusing to mint")
        return False
    log(f"  [gate] exit {ip} · {isp or 'residential'} · BD ok")
    return True


def load_pool() -> list:
    try:
        data = json.loads(POOL.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_pool(pool: list) -> None:
    POOL.write_text(json.dumps(pool, indent=2))


# Every account should read "SBㅤXXXXX": the owner's initials, then U+3164 HANGUL
# FILLER, which renders as nothing at all in game, then random characters. The
# separator is a real character, not a space, so the game font shows no gap.
LEGACY_LOGIN = False          # --legacy: replay the exact path that made working accounts
NICK_PREFIX = os.environ.get("GUEST_NICK_PREFIX", "SB")
NICK_SEP = os.environ.get("GUEST_NICK_SEP", "\u3164")


def rand_tail(n: int = 5) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def make_nick() -> str:
    return f"{NICK_PREFIX}{NICK_SEP}{rand_tail()}"


def mint_one(sess, vdata: dict) -> dict | None:
    """One account, using the modules that already work."""
    import full_login
    from guests_manager import game_register as gr

    cred, why = gr.msdk_register(sess)
    if not cred:
        log(f"    register failed: {why}")
        return None
    uid, pw = cred
    log(f"    registered uid {uid}")

    at, oid = gr.token_grant(sess, uid, pw)
    if not at:
        log("    grant failed")
        return None

    # nickname: SB + invisible filler + random, as the owner asked. Registration
    # is strict (a bad nickname answers with a short "NO..." body and the login
    # that follows 404s), so if the server refuses it we retry once with the
    # proven ANON bullet form rather than throwing the guest away.
    nick = make_nick()
    ok, msg = gr.major_register(sess, at, oid, nick)
    if not ok:
        log(f"    nickname {nick!r} refused ({msg}), retrying as ANON bullet")
        nick = "ANON\u2022" + rand_tail()
        ok, msg = gr.major_register(sess, at, oid, nick)
    if not ok:
        log(f"    major_register: {msg}")
        return None

    # MajorLogin -> ChooseRegion(BD) -> MajorLogin #2. The middle step is what
    # gives the account its own game host and a real profile; without it the
    # account shows up as a foreign region and its likes are never credited.
    reg = str(vdata.get("region") or "BD").upper()
    if LEGACY_LOGIN:
        # Owner's correction (2026-11-05): the accounts minted last night ARE BD
        # accounts - cross-region search would not have found them from a BD
        # login, and his account proved it. Only the in-game language showed
        # Indonesia. So replay that exact path: no ChooseRegion, the same
        # language field as before, and the login host as the game host.
        info = full_login.full_major_login(uid, oid, at, release=vdata["release"], region="ID")
        if not info:
            log("    major login failed")
            return None
        for _ in range(2):
            again = full_login.full_major_login(uid, oid, at, release=vdata["release"], region="ID")
            if again and len(again.get("jwt", "")) > len(info.get("jwt", "")):
                info = again
            if len(info.get("jwt", "")) >= 815:
                break
        srv = str(info.get("server_url") or info.get("url") or "").rstrip("/")
        if not srv or "loginbp" in srv:
            srv = "https://" + str(vdata["login_host"]).replace("loginbp", "clientbp")
        log(f"    legacy path (no ChooseRegion) - game host {srv}")
        locked = True
    else:
        info = full_login.login_locked(uid, oid, at, region=reg, release=vdata["release"])
        if not info:
            log("    major login failed")
            return None
        for _ in range(2):
            again = full_login.login_locked(uid, oid, at, region=reg, release=vdata["release"])
            if again and len(again.get("jwt", "")) > len(info.get("jwt", "")):
                info = again
            if len(info.get("jwt", "")) >= 815:
                break
        srv = str(info.get("server_url") or "").rstrip("/")
        locked = bool(srv)
        if not locked:
            log("    region NOT locked - no server_url, so this account cannot be profiled")
    key = info.get("key")
    iv = info.get("iv")
    key = key.hex() if isinstance(key, (bytes, bytearray)) else key
    iv = iv.hex() if isinstance(iv, (bytes, bytearray)) else iv
    rec = {
        "uid": uid, "pass": pw, "token": info.get("jwt", ""), "nick": nick,
        "account_id": info.get("account_id"), "region": reg if locked else "UNLOCKED",
        "key": key, "iv": iv, "server_url": srv,
        "release": vdata["release"], "ts": int(time.time()),
        "ts_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        "profiled": False,
    }
    if "clientbp" not in srv:
        log(f"    note: game host is {srv} (no clientbp in the name)")
    log(f"    logged in: jwt {len(rec['token'])} chars on {srv}")
    return rec


def give_profile(rec: dict) -> bool:
    """GetLoginData + presence: this is what turns a login into a real profile."""
    import asyncio
    import supply
    if not rec.get("server_url"):
        log("    no game host on this record - skipping presence")
        return False
    return asyncio.run(supply.enter_game(rec))


def main() -> int:
    ap = argparse.ArgumentParser(description="our own BD guest account factory")
    ap.add_argument("--count", type=int, default=1, help="how many accounts to create")
    ap.add_argument("--profile", action="store_true", help="run GetLoginData + presence after minting")
    ap.add_argument("--proxy", default=None, help="BD socks/http proxy (default: $BD_PROXY_URL)")
    ap.add_argument("--gap", type=float, default=45.0, help="seconds between registrations")
    ap.add_argument("--probe", action="store_true", help="print live version data and exit")
    ap.add_argument("--no-ip-gate", action="store_true", help="skip the BD IP check (tests only)")
    ap.add_argument("--fresh-vercfg", action="store_true")
    ap.add_argument("--legacy", action="store_true",
                    help="replay last night's login path (no ChooseRegion)")
    args = ap.parse_args()
    global LEGACY_LOGIN
    LEGACY_LOGIN = bool(args.legacy)

    vdata = version_data(force=args.fresh_vercfg)
    log(f"version data · release {vdata['release']} · login {vdata['login_host']} · "
        f"server_open {vdata.get('server_open')}")
    if args.probe:
        return 0

    sess = make_session(args.proxy)
    ip = exit_ip(sess)
    if not args.no_ip_gate and not is_bd_ip(ip):
        log("refusing to mint: traffic is not leaving from a Bangladesh residential IP")
        return 2

    pool = load_pool()
    seen = {r.get("uid") for r in pool}
    made, throttled = [], 0

    for i in range(1, args.count + 1):
        log(f"  [{i}/{args.count}]")
        rec = mint_one(sess, vdata)
        if not rec:
            throttled += 1
            back = min(300, 60 * throttled)
            log(f"    backing off {back}s (Garena throttles bursts)")
            time.sleep(back)
            continue

        # save the account BEFORE the profile step: a crash in presence must
        # never cost us an account that Garena has already created
        if rec["uid"] not in seen:
            pool.append(rec)
            seen.add(rec["uid"])
            made.append(rec["uid"])
            save_pool(pool)

        if args.profile:
            try:
                ok = give_profile(rec)
            except Exception as exc:
                ok = False
                log(f"    profile step failed: {type(exc).__name__}: {str(exc)[:90]}")
            rec["profiled"] = bool(ok)
            save_pool(pool)
            log(f"    profile {'created' if ok else 'NOT created'} (account saved either way)")

        if i < args.count:
            time.sleep(args.gap)

    log(json.dumps({"minted": len(made), "uids": made, "failed": throttled,
                    "pool_size": len(pool), "release": vdata["release"]}, indent=2))
    return 0 if made else 4


if __name__ == "__main__":
    sys.exit(main())
