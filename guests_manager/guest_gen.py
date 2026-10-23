#!/usr/bin/env python3
"""
Free Fire Guest Account Generator — server-side, no APK needed.
Verified working 2026-10-23 (breakthrough: signatureKey = client_secret ASCII bytes).

Flow:
  1. POST /api/v2/oauth/guest:register  (JSON, signed) -> uid
  2. POST /api/v2/oauth/guest/token:grant (JSON, signed) -> access_token + open_id
Usage:
  python3 guest_gen.py [count]         # default 1
"""
import httpx, hmac, hashlib, secrets, string, json, sys, time, random, warnings
from pathlib import Path
warnings.filterwarnings("ignore")

HEX_KEY = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
API_KEY = HEX_KEY.encode()  # ASCII bytes of the hex string — this is the signing key
REGISTER_URL = "https://100067.connect.garena.com/api/v2/oauth/guest:register"
GRANT_URL = "https://100067.connect.garena.com/api/v2/oauth/guest/token:grant"
UA_REGISTER = "GarenaMSDK/4.0.39(SM-A325M ;Android 13;en;HK;)"
UA_GRANT = "GarenaMSDK/4.0.19P10(I2404 ;Android 15;en;US;)"
ACCOUNTS_FILE = Path(__file__).parent / "guests.json"


def sign(body: str) -> str:
    return hmac.new(API_KEY, body.encode(), hashlib.sha256).hexdigest()


def gen_password() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(16))


def register_guest() -> dict:
    pw = gen_password()
    payload = json.dumps(
        {"app_id": 100067, "client_type": 2, "password": pw, "source": 2},
        separators=(",", ":"),
    )
    headers = {
        "User-Agent": UA_REGISTER,
        "Authorization": f"Signature {sign(payload)}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "Connection": "Keep-Alive",
        "Host": "100067.connect.garena.com",
    }
    r = httpx.post(REGISTER_URL, headers=headers, data=payload, timeout=15, verify=False)
    j = r.json() if r.status_code == 200 else {}
    if r.status_code != 200 or not j.get("data", {}).get("uid"):
        raise RuntimeError(f"register failed: {r.status_code} {r.text[:150]}")
    return {"uid": str(j["data"]["uid"]), "password": pw}


def grant_token(uid: str, password: str) -> dict:
    data = json.dumps(
        {
            "client_id": 100067, "client_secret": HEX_KEY, "client_type": 2,
            "password": password, "response_type": "token", "uid": int(uid),
        },
        separators=(",", ":"),
    )
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": UA_GRANT,
        "Authorization": f"Signature {sign(data)}",
    }
    r = httpx.post(GRANT_URL, headers=headers, data=data, timeout=15, verify=False)
    j = r.json() if r.status_code == 200 else {}
    d = j.get("data", {})
    if r.status_code != 200 or not d.get("access_token"):
        raise RuntimeError(f"grant failed: {r.status_code} {r.text[:150]}")
    return {
        "access_token": d["access_token"],
        "open_id": d.get("open_id", ""),
        "expiry_time": d.get("expiry_time"),
    }


def create_guest(pace_seconds: float = 15.0) -> dict:
    g = register_guest()
    time.sleep(random.uniform(0.5, 1.5))
    tok = grant_token(g["uid"], g["password"])
    acct = {**g, **tok, "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    save(acct)
    return acct


def save(acct: dict):
    data = json.loads(ACCOUNTS_FILE.read_text()) if ACCOUNTS_FILE.exists() else []
    data.append(acct)
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    ok = 0
    for i in range(n):
        try:
            a = create_guest()
            ok += 1
            print(f"[{i+1}/{n}] OK uid={a['uid']} token={a['access_token'][:16]}...")
        except Exception as e:
            print(f"[{i+1}/{n}] FAIL {e}")
        if i < n - 1:
            time.sleep(pace if (pace := 15.0) else 15.0)
    print(f"done: {ok}/{n} guests -> {ACCOUNTS_FILE}")


if __name__ == "__main__":
    main()
