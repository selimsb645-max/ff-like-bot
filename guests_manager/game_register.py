#!/usr/bin/env python3
"""Game-account registration pipeline (BD region target).

Converts stockpiled guests (guests.json) + freshly-registered guests into
game accounts via MajorRegister, verifies with MajorLogin, tags region,
and appends to registered_pool.json. Stops at TARGET total.

REGION SAFETY: the FIRST account's lock_region is checked. If it is not BD,
the run ABORTS unless ALLOW_ANY_REGION=1 — registering from a non-BD IP
permanently locks accounts as NA/EU (useless for BD likes/guild).
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import random
import secrets
import string
import sys
import time
import urllib.request
from pathlib import Path

import requests
import urllib3
from Crypto.Cipher import AES

urllib3.disable_warnings()

ROOT = Path(__file__).parent.parent
GUESTS_FILE = Path(__file__).parent / "guests.json"
REG_FILE = Path(__file__).parent / "registered_pool.json"
TARGET = int(os.environ.get("TARGET_ACCOUNTS", "1000"))

AES_KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
AES_IV = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])
HEX_KEY = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"

MSDK_REGISTER = "https://100067.connect.garena.com/api/v2/oauth/guest:register"
MSDK_GRANT = "https://100067.connect.garena.com/api/v2/oauth/guest/token:grant"
MAJOR_REGISTER_HOSTS = ["loginbp.ppmainecoonghj.com", "loginbp.ggpolarbear.com"]
MAJOR_LOGIN = "https://loginbp.ggpolarbear.com/MajorLogin"

FIELD_22 = bytes.fromhex(
    "4747524501010100620200001052aa0d669c6a368f08338060d2ee0690053af84a41edcd3558556ec10f24f4"
    "6c93ac64ca41a16732c46a2cb071246a79b8929032f9e1b6f4ef331bd53cabf29b09b97349a46e9863c0314e"
    "1a0d80819fef8aabf03876b3d037db354a7ccb5c1bce96411fb3753f6f50e44c69c4ed617fa30efb8ffc0517"
    "ff2f636739be1f304d999cfd6fd48bf69454199794c3dc88f55a4bdbd66534d5a061359cdfd1fb680cd37918"
    "df9fdb3cf7d80067b0a3506c90063cf62b2ccec11e23913a2fd7c4ef091331967bb518a5ad1e551146b90821"
    "be800883abadde39d6c80a5d798611466c748f075481806c5842ce45e6bd4e3368ec08fe2ec41ceb880cd862"
    "49eb71693f79f0bccf9e590c3fae12519fe08c7a1905d0927690109e0df28574bb14847225db1a59230e6662"
    "ed7730e15ff9a6c815cb41b420edeada735a4b03e181037c37c2c850257311df2f07b0a56e759372cbd0268e"
    "3f13a292ee4373e38ab5096e0342a5e0d7fec6da2bbc265d74baadd2b24ee4f74862f82c21d6694bac53f8ce"
    "80312a30068a6276a641c19b11d0305c6fe2f531ac7de578b29f543697f5c73663e6f23aa15277b6122dcd4d"
    "4171e38f9ac0b173f39c58416a16c5c1f4a35acd065ce78f449cf538a249339e763272d458e4ed86c976591a"
    "9c066b3a37111e44091eb6b5a795249f3e5145db022a6055f2cc675936391312f688f89627845df222a91156"
    "555225be36f9714a0ba50246d0f003bda3c9c1292ab73b4f79635ddd023218eda93a302d79e023404c143965"
    "44a930b98ed54771aca7fec10d095587685b473e81a9619764fad9256529dcd6e911f4f4629612287d4ee3ec"
    "5389f6ec4ec020b0e2aac017232a9197be9e46239ce690fe5d4872b2e98e651510c971667f3aca8b59f3e9d5"
    "0e43"
)
XOR_KEYSTREAM = [0x30, 0x30, 0x30, 0x32, 0x30, 0x31, 0x37, 0x30, 0x30, 0x30, 0x30, 0x30, 0x32, 0x30, 0x31, 0x37,
                 0x30, 0x30, 0x30, 0x30, 0x30, 0x32, 0x30, 0x31, 0x37, 0x30, 0x30, 0x30, 0x30, 0x30, 0x32, 0x30]


def pad16(b: bytes) -> bytes:
    n = 16 - (len(b) % 16)
    return b + bytes([n]) * n


def encrypt_payload(b: bytes) -> bytes:
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad16(b))


def evb(v: int) -> bytes:
    out = b""
    while True:
        x = v & 0x7F
        v >>= 7
        if v:
            out += bytes([x | 0x80])
        else:
            out += bytes([x])
            return out


def pf(field: int, val):
    if isinstance(val, int):
        return evb((field << 3) | 0) + evb(val)
    v = val.encode() if isinstance(val, str) else val
    return evb((field << 3) | 2) + evb(len(v)) + v


def msdk_register(session):
    pw = "".join(secrets.choice("0123456789abcdef") for _ in range(32))
    body = json.dumps({"app_id": 100067, "client_type": 2, "password": pw, "source": 2})
    sig = hmac.new(HEX_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()
    r = session.post(MSDK_REGISTER, data=body, timeout=15,
        headers={"User-Agent": "GarenaMSDK/4.0.39(SM-A325M ;Android 13;en;HK;)",
                 "Authorization": "Signature " + sig,
                 "Content-Type": "application/json; charset=utf-8"})
    if r.status_code != 200:
        return None, ("429" if r.status_code == 429 else f"http{r.status_code}")
    return (r.json()["data"]["uid"], pw), None


def token_grant(session, uid, pw):
    import uuid as _uuid
    r = session.post(MSDK_GRANT, json={"client_id": 100067, "client_secret": HEX_KEY,
        "client_type": 2, "device_id": "02-" + str(_uuid.uuid4()), "password": pw,
        "response_type": "token", "uid": int(uid)}, timeout=15,
        headers={"User-Agent": "GarenaMSDK/4.0.19P10(I2404 ;Android 15;en;US;)"})
    if r.status_code != 200:
        return None, None
    d = r.json()["data"]
    return d.get("access_token"), d.get("open_id")


def major_register(session, at, oid, nickname):
    enc_oid = "".join(chr(ord(c) ^ XOR_KEYSTREAM[i % 32]) for i, c in enumerate(oid))
    proto = (pf(1, nickname) + pf(2, at) + pf(3, oid) + pf(5, 102000007) + pf(6, 4)
             + pf(7, 1) + pf(13, 1) + pf(14, enc_oid.encode("latin1")) + pf(15, "bn")
             + pf(16, 1) + pf(17, 1) + pf(22, FIELD_22))
    h = {"User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)",
         "Accept-Encoding": "deflate, gzip", "X-GA-SV": "1789535859", "Authorization": "Bearer",
         "X-GA": "v1 1", "ReleaseVersion": "OB55",
         "Content-Type": "application/x-www-form-urlencoded",
         "X-Unity-Version": "2018.4.12f1"}
    for host in MAJOR_REGISTER_HOSTS:
        try:
            r = session.post(f"https://{host}/MajorRegister", headers=h,
                             data=encrypt_payload(proto), timeout=20, verify=False)
            if r.status_code == 200:
                return True, "ok"
            txt = r.content[:30].decode("utf-8", "replace")
            if "ACCOUNT_EXIST" in txt:
                return True, "exists"
            return False, txt
        except Exception as e:
            return False, str(e)[:40]
    return False, "no_host"


def major_login(session, at, oid):
    wire = (pf(22, oid) + pf(23, "4") + pf(29, at) + pf(99, "4"))
    h = {"User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
         "Accept-Encoding": "gzip", "X-GA-SV": "1789638359",
         "Content-Type": "application/x-www-form-urlencoded",
         "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1", "ReleaseVersion": "OB55"}
    r = session.post(MAJOR_LOGIN, headers=h, data=encrypt_payload(wire), timeout=20, verify=False)
    if r.status_code != 200 or b"eyJ" not in r.content:
        return None
    return re_jwt(r.content)


def re_jwt(content: bytes):
    import re as _re
    m = _re.search(rb"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", content)
    return m.group(0).decode() if m else None


def jwt_region(jwt):
    try:
        claims = json.loads(base64.urlsafe_b64decode(jwt.split(".")[1] + "==").decode())
        return claims.get("lock_region", "?")
    except Exception:
        return "?"


def gh_commit(path, content, msg):
    tok = os.environ.get("GITHUB_USAGE_TOKEN")
    if not tok:
        return
    try:
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
    except Exception:
        pass


PROXIES_FILE = Path(__file__).parent / "proxies.txt"
ACTIVE_PROXY = {"url": None}


def proxy_candidates():
    try:
        return [x.strip() for x in PROXIES_FILE.read_text().split("\n") if x.strip()]
    except Exception:
        return []


def check_proxy(url):
    """Verify exit IP is in BD via ipinfo (through the proxy)."""
    try:
        r = requests.get("https://ipinfo.io/json",
                         proxies={"http": url, "https": url}, timeout=12)
        d = r.json()
        return d.get("country") == "BD", d.get("ip"), d.get("country")
    except Exception:
        return False, None, None


def pick_proxy():
    for px in proxy_candidates():
        url = "http://" + px
        ok, ip, country = check_proxy(url)
        print(f"proxy {px}: exit={ip} country={country} {'OK' if ok else 'reject'}", flush=True)
        if ok:
            ACTIVE_PROXY["url"] = url
            return url
    return None


def new_session():
    s = requests.Session()
    if ACTIVE_PROXY["url"]:
        s.proxies = {"http": ACTIVE_PROXY["url"], "https": ACTIVE_PROXY["url"]}
    return s


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hours", type=float, default=4.6)
    args = ap.parse_args()
    allow_any = os.environ.get("ALLOW_ANY_REGION", "") == "1"
    end_at = time.time() + args.max_hours * 3600

    px = pick_proxy()
    if not px:
        print("NO WORKING BD PROXY — aborting (accounts would lock to the wrong region). "
              "Update guests_manager/proxies.txt", flush=True)
        return
    print("using BD proxy:", px, flush=True)

    registered = load_json(REG_FILE, [])
    done_uids = {str(r.get("uid")) for r in registered}
    stock = [g for g in load_json(GUESTS_FILE, []) if str(g.get("uid")) not in done_uids]

    print(f"registered={len(registered)}/{TARGET}, stockpile={len(stock)}, "
          f"allow_any_region={allow_any}", flush=True)

    def save():
        Path(REG_FILE).write_text(json.dumps(registered, indent=0))

    count = 0
    fail_run = 0
    first = True
    while len(registered) < TARGET and time.time() < end_at:
        s = new_session()
        # pick from stockpile or create fresh
        if stock:
            g = stock.pop(0)
            uid, pw = str(g["uid"]), g["password"]
            fresh = False
        else:
            for _ in range(3):
                got, err = msdk_register(s)
                if got:
                    uid, pw = str(got[0]), got[1]
                    fresh = True
                    break
                if err == "429":
                    print("MSDK 429, waiting 70s", flush=True)
                    time.sleep(70)
            else:
                print("msdk register exhausted retries", flush=True)
                break

        at, oid = token_grant(s, uid, pw)
        if not at:
            print(f"{uid} grant failed", flush=True)
            fail_run += 1
            if fail_run >= 10:
                print("10 consecutive failures, re-picking proxy", flush=True)
                ACTIVE_PROXY["url"] = None
                px = pick_proxy()
                if not px:
                    print("no working BD proxy left, stopping", flush=True)
                    break
                fail_run = 0
            time.sleep(5)
            continue
        nick = "Kexin" + "".join(random.choices(string.digits, k=4))
        ok, msg = major_register(s, at, oid, nick)
        time.sleep(1.5)
        jwt = major_login(s, at, oid)
        if not jwt:
            print(f"{uid} register {msg}, login FAIL (skip)", flush=True)
            time.sleep(4)
            continue
        region = jwt_region(jwt)
        if first:
            first = False
            if region != "BD" and not allow_any:
                print(f"ABORT: first account region={region} (not BD). "
                      f"Registering from this IP would permanently lock ALL accounts "
                      f"as {region}. Set ALLOW_ANY_REGION=1 to override.", flush=True)
                return
        registered.append({"uid": uid, "pw": pw, "region": region, "nick": nick,
                           "verified": int(time.time())})
        count += 1
        fail_run = 0
        save()
        print(f"[{len(registered)}/{TARGET}] {uid} region={region} ({msg})", flush=True)
        time.sleep(8 if not fresh else 70)
        if count % 15 == 0:
            gh_commit("guests_manager/registered_pool.json",
                      json.dumps(registered, indent=0), f"registered {len(registered)}/{TARGET}")

    save()
    gh_commit("guests_manager/registered_pool.json",
              json.dumps(registered, indent=0), f"registered {len(registered)}/{TARGET}")
    print(f"DONE: {len(registered)}/{TARGET} registered", flush=True)


if __name__ == "__main__":
    main()
