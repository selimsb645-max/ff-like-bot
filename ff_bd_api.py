#!/usr/bin/env python3
"""
FF BD API — stats + likes via JWT pool.
Endpoints verified 2026-10-23:
  GetPlayerPersonalShow / LikeProfile @ clientbp.ggpolarbear.com (OB53/55, Bearer JWT)
  JWT pool: external minting service (uid+pass -> fresh JWT per request)
Target uid = BD player's account_id (11-digit, as shown in profile).
"""
import httpx, json, time, warnings, binascii, base64
from pathlib import Path
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

warnings.filterwarnings("ignore")
AES_KEY = b"Yg&tc%DEuh6%Zc^8"
AES_IV = b"6oyZDr22E3ychjM%"
PROFILE_URL = "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"
LIKE_URL = "https://clientbp.ggpolarbear.com/LikeProfile"
JWT_SERVICE = "https://sheihk-jwt-ob55.up.railway.app/token"
POOL_FILE = Path(__file__).parent / "bd_pool.json"
CREDS_FILE = Path(__file__).parent / "bd_creds.txt"

# proto: profile req {1: account_id, 2: 1}
# proto: like req {1: account_id, 2: "BD"}


def _ev(v: int) -> bytes:
    out = bytearray()
    while True:
        c = v & 0x7F
        v >>= 7
        if v:
            c |= 0x80
        out.append(c)
        if not v:
            return bytes(out)


def _pb(f: int, val) -> bytes:
    if isinstance(val, int):
        return _ev((f << 3) | 0) + _ev(val)
    if isinstance(val, str):
        val = val.encode()
    return _ev((f << 3) | 2) + _ev(len(val)) + val


def _enc(pt: bytes) -> bytes:
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(pt, 16))


def _headers(jwt: str, rv: str = "OB55") -> dict:
    return {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_I005D Build/PI)",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Expect": "100-continue",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": rv,
    }


def load_creds() -> list:
    if not CREDS_FILE.exists():
        return []
    creds = []
    for line in CREDS_FILE.read_text().splitlines():
        line = line.strip()
        if line and ":" in line and not line.startswith("#"):
            uid, pw = line.split(":", 1)
            creds.append((uid.strip(), pw.strip()))
    return creds


def mint_jwt(uid: str, pw: str) -> dict | None:
    """Fetch a fresh JWT for a known account from the minting service."""
    try:
        r = httpx.get(JWT_SERVICE, params={"uid": uid, "pass": pw}, timeout=20, verify=False)
        j = r.json()
        if j.get("Status") != "success":
            return None
        tok = j["Token"]
        pl = tok.split(".")[1] + "=" * (-len(tok.split(".")[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pl))
        return {
            "uid": uid, "pass": pw, "token": tok,
            "account_id": claims.get("account_id"),
            "region": claims.get("lock_region", "BD"),
            "exp": claims.get("exp"),
            "level": j.get("Level"), "name": j.get("Name"),
        }
    except Exception:
        return None


def refresh_pool() -> list:
    """Mint fresh JWTs for all pool accounts."""
    pool = []
    for uid, pw in load_creds():
        t = mint_jwt(uid, pw)
        if t:
            pool.append(t)
    POOL_FILE.write_text(json.dumps(pool, indent=1))
    return pool


def get_pool(force: bool = False) -> list:
    if force or not POOL_FILE.exists():
        return refresh_pool()
    pool = json.loads(POOL_FILE.read_text())
    now = time.time()
    if not pool or all(t.get("exp", 0) < now + 60 for t in pool):
        return refresh_pool()
    return pool


def _decode_profile(data: bytes) -> dict:
    """Minimal protobuf decode of profile response (AccountInfo)."""
    out = {}
    i = 0
    n = len(data)

    def rd_varint(p):
        v = s = 0
        while p < n:
            b = data[p]
            p += 1
            v |= (b & 0x7F) << s
            if not b & 0x80:
                return v, p
            s += 7
        return 0, p

    def walk(buf, depth=0):
        res = {}
        p = 0
        while p < len(buf):
            tag, p = rd_varint(p) if False else (buf[p], p + 1) if False else rd_varint_in(buf, p)
            fnum, wt = tag >> 3, tag & 7
            if wt == 0:
                v, p = rd_varint_in(buf, p)
                res[fnum] = v
            elif wt == 2:
                ln, p = rd_varint_in(buf, p)
                res[fnum] = buf[p:p + ln]
                p += ln
            else:
                break
        return res

    def rd_varint_in(buf, p):
        v = s = 0
        while p < len(buf):
            b = buf[p]
            p += 1
            v |= (b & 0x7F) << s
            if not b & 0x80:
                return v, p
            s += 7
        return 0, p

    top = walk(data)
    ai = top.get(1)  # field 1 = AccountInfo submessage
    if ai:
        f = walk(ai)
        out["uid"] = f.get(1)
        out["name"] = f.get(2, b"").decode("utf-8", "replace") if isinstance(f.get(2), bytes) else f.get(2)
        out["likes"] = f.get(4)
        out["level"] = f.get(6)
        out["region"] = f.get(3, b"").decode("utf-8", "replace") if isinstance(f.get(3), bytes) else f.get(3)
        out["premium_level"] = f.get(12)
        out["bio"] = f.get(20, b"").decode("utf-8", "replace") if isinstance(f.get(20), bytes) else None
    return out


def get_stats(account_id: int, jwt: str = None) -> dict | None:
    """Fetch BD player profile. account_id = displayed UID."""
    import like_count_pb2 as lcpb
    import uid_generator_pb2 as ugp
    if not jwt:
        pool = get_pool()
        if not pool:
            return None
        jwt = pool[0]["token"]
    m = ugp.uid_generator()
    m.krishna_ = int(account_id)
    m.teamXdarks = 1
    payload = _enc(m.SerializeToString())
    r = httpx.post(PROFILE_URL, headers=_headers(jwt, "OB53"), data=payload, timeout=25, verify=False)
    if r.status_code != 200:
        msg = r.content[:60].decode("utf-8", "replace").strip()
        return {"error": msg or f"HTTP {r.status_code}"}
    info = lcpb.Info()
    try:
        info.ParseFromString(r.content)
    except Exception:
        return {"error": "parse failed"}
    ai = info.AccountInfo
    return {"uid": str(ai.UID), "name": ai.PlayerNickname, "likes": int(ai.Likes)}


def send_like(account_id: int, count: int = 1, used: set = None) -> int:
    """Send likes to a target. Each pool account can like once per target."""
    pool = get_pool()
    used = used if used is not None else set()
    sent = 0
    for t in pool:
        if sent >= count:
            break
        key = (t["account_id"], account_id)
        if key in used:
            continue
        import like_pb2 as lpb
        m = lpb.like()
        m.uid = int(account_id)
        m.region = "BD"
        payload = _enc(m.SerializeToString())
        r = httpx.post(LIKE_URL, headers=_headers(t["token"], "OB55"), data=payload, timeout=25, verify=False)
        if r.status_code == 200:
            used.add(key)
            sent += 1
    return sent


if __name__ == "__main__":
    pool = refresh_pool()
    print(f"pool: {len(pool)} BD accounts")
    for t in pool[:5]:
        s = get_stats(t["account_id"], t["token"])
        print(f"  {t['account_id']} lvl={s.get('level')} likes={s.get('likes')} name={s.get('name')}")
