#!/usr/bin/env python3
"""
FF BD API — stats + likes via JWT pool.
Endpoints verified 2026-10-23:
  GetPlayerPersonalShow / LikeProfile @ clientbp.ggpolarbear.com (OB53/55, Bearer JWT)
  JWT pool: external minting service (uid+pass -> fresh JWT per request)
Target uid = BD player's account_id (11-digit, as shown in profile).
"""
import httpx, json, time, re, warnings, binascii, base64
from pathlib import Path
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

warnings.filterwarnings("ignore")
AES_KEY = b"Yg&tc%DEuh6%Zc^8"
AES_IV = b"6oyZDr22E3ychjM%"
PROFILE_URL = "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"
LIKE_URL = "https://clientbp.ggpolarbear.com/LikeProfile"
JWT_SERVICE = "https://sheihk-jwt-ob55.up.railway.app/token"

# Live public token pools (community like-bots that auto-refresh their JWTs to GitHub)
EXTERNAL_TOKEN_URLS = [
    "https://raw.githubusercontent.com/riyadcoderservice-droid/autolike-api-frexy/main/token_bd.json",
]
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


# ── Self-minted JWT (MajorLogin, verified 2026-10-23) ──────────────
GRANT_URL = "https://100067.connect.garena.com/api/v2/oauth/guest/token:grant"
MAJOR_LOGIN_URL = "https://loginbp.ggpolarbear.com/MajorLogin"
CLIENT_SECRET = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
LOGIN_UA = "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)"


def _evb(v: int) -> bytes:
    out = bytearray()
    while True:
        c = v & 0x7F
        v >>= 7
        if v:
            c |= 0x80
        out.append(c)
        if not v:
            return bytes(out)


def _pf_str(field: int, s: str) -> bytes:
    b = s.encode()
    return _evb((field << 3) | 2) + _evb(len(b)) + b


def guest_grant(uid: str, pw: str):
    """OAuth guest token grant -> (access_token, open_id) or None."""
    try:
        r = httpx.post(GRANT_URL, json={
            "client_id": 100067, "client_secret": CLIENT_SECRET, "client_type": 2,
            "password": pw, "response_type": "token", "uid": int(uid)},
            headers={"User-Agent": "GarenaMSDK/4.0.19P10(I2404 ;Android 15;en;US;)"},
            timeout=15, verify=False)
        if r.status_code != 200:
            return None
        d = r.json().get("data", r.json())
        at, oid = d.get("access_token"), d.get("open_id")
        return (at, oid) if at and oid else None
    except Exception:
        return None


def major_login(access_token: str, open_id: str) -> str | None:
    """MajorLogin (OB55, dummy X-GA-SV) -> JWT or None."""
    try:
        wire = (_pf_str(22, open_id) + _pf_str(23, "4")
                + _pf_str(29, access_token) + _pf_str(99, "4"))
        body = _enc(wire)
        h = {"User-Agent": LOGIN_UA, "Connection": "Keep-Alive", "Accept-Encoding": "gzip",
             "X-GA-SV": "1789638359", "Content-Type": "application/x-www-form-urlencoded",
             "Expect": "100-continue", "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1",
             "ReleaseVersion": "OB55"}
        r = httpx.post(MAJOR_LOGIN_URL, headers=h, data=body, timeout=20, verify=False)
        if r.status_code != 200:
            return None
        m = re.search(rb"(eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]{43})", r.content)
        if not m:
            return None
        return m.group(1).decode()
    except Exception:
        return None


def mint_jwt_self(uid: str, pw: str) -> dict | None:
    """Mint a JWT for a known game account without any external service."""
    tok_pair = guest_grant(uid, pw)
    if not tok_pair:
        return None
    tok = major_login(*tok_pair)
    if not tok:
        return None
    pl = tok.split(".")[1]
    pl += "=" * (-len(pl) % 4)
    claims = json.loads(base64.urlsafe_b64decode(pl))
    return {"uid": uid, "pass": pw, "token": tok,
            "account_id": claims.get("account_id"),
            "region": claims.get("lock_region", "BD"),
            "exp": claims.get("exp")}

def mint_jwt(uid: str, pw: str) -> dict | None:
    """Fresh JWT: self-mint via MajorLogin first, external service as fallback."""
    self_tok = mint_jwt_self(uid, pw)
    if self_tok:
        self_tok["level"] = None
        self_tok["name"] = None
        return self_tok
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




# ── Rank tier tables (from FF rank_data, OB55) ─────────────────────
BR_TIERS = [
    (700, 800, "Bronze I"), (801, 900, "Bronze II"), (901, 1000, "Bronze III"),
    (1001, 1100, "Silver I"), (1101, 1200, "Silver II"), (1201, 1300, "Silver III"),
    (1301, 1400, "Gold I"), (1401, 1500, "Gold II"), (1501, 1600, "Gold III"),
    (1601, 1800, "Gold IV"),
    (1801, 1900, "Platinum I"), (1901, 2000, "Platinum II"), (2001, 2100, "Platinum III"),
    (2101, 2300, "Platinum IV"),
    (2301, 2400, "Diamond I"), (2401, 2500, "Diamond II"), (2501, 2600, "Diamond III"),
    (2601, 3200, "Diamond IV"),
    (3201, 6000, "Heroic"), (6001, 999999, "Master"),
]
CS_TIERS = [
    (0, 2, "Bronze I"), (3, 5, "Bronze II"), (6, 8, "Bronze III"),
    (9, 12, "Silver I"), (13, 16, "Silver II"), (17, 20, "Silver III"),
    (21, 25, "Gold I"), (26, 30, "Gold II"), (31, 35, "Gold III"), (36, 40, "Gold IV"),
    (41, 45, "Platinum I"), (46, 50, "Platinum II"), (51, 55, "Platinum III"),
    (56, 60, "Platinum IV"),
    (61, 65, "Diamond I"), (66, 70, "Diamond II"), (71, 75, "Diamond III"),
    (76, 80, "Diamond IV"), (81, 85, "Diamond V"),
    (86, 110, "Heroic"), (111, 135, "Elite Heroic"), (136, 156, "Master"),
    (157, 99999, "Grandmaster"),
]


def _tier_name(points, table):
    for lo, hi, name in table:
        if lo <= points <= hi:
            return name
    return "-"


def _pb_walk(buf):
    """Full protobuf walk preserving repeated fields. Returns {fnum: [(wt, value)]}."""
    out = {}
    p, n = 0, len(buf)

    def rv(p):
        v = s = 0
        while p < n:
            b = buf[p]; p += 1
            v |= (b & 0x7F) << s
            if not b & 0x80:
                return v, p
            s += 7
        return 0, p

    while p < n:
        try:
            tag, p = rv(p)
            fnum, wt = tag >> 3, tag & 7
            if wt == 0:
                v, p = rv(p)
                out.setdefault(fnum, []).append(("i", v))
            elif wt == 2:
                ln, p = rv(p)
                out.setdefault(fnum, []).append(("b", buf[p:p + ln]))
                p += ln
            else:
                break
        except Exception:
            break
    return out


def _first(d, fnum):
    v = d.get(fnum, [])
    return v[0][1] if v else None


def get_profile(account_id, jwt: str = None) -> dict | None:
    """Full player card: nickname, level, region, BR/CS rank, guild, pet, credit score."""
    t = jwt or (get_pool() or [{}])[0].get("token")
    if not t:
        return None
    wire = _pb(1, int(account_id)) + _pb(2, 1)
    h = dict(_headers(t))
    h["ReleaseVersion"] = "OB53"
    h["X-GA-SV"] = "1789556611"
    try:
        r = httpx.post(PROFILE_URL, headers=h, data=_enc(wire), timeout=20, verify=False)
    except Exception:
        return None
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    try:
        top = _pb_walk(r.content)
    except Exception:
        return None
    basic = _pb_walk(_first(top, 1)) if _first(top, 1) else {}
    out = {}
    out["uid"] = _first(basic, 1)
    nick = _first(basic, 3)
    if isinstance(nick, bytes):
        # nickname may be a nested msg (styled) — try direct utf-8 then nested field
        try:
            out["name"] = nick.decode("utf-8")
        except Exception:
            inner = _pb_walk(nick)
            nb = _first(inner, 1)
            if nb is None:
                for k in inner:
                    if isinstance(inner[k][0][1], bytes):
                        nb = inner[k][0][1]
                        break
            out["name"] = nb.decode("utf-8", "replace") if isinstance(nb, bytes) else str(nb or "?")
    else:
        out["name"] = nick
    out["region"] = (_first(basic, 5) or b"").decode() if isinstance(_first(basic, 5), bytes) else _first(basic, 5)
    out["level"] = _first(basic, 6)
    out["exp"] = _first(basic, 7)
    out["likes"] = _first(basic, 21)
    out["last_login"] = _first(basic, 24)
    out["create_at"] = _first(basic, 44)
    rv = _first(basic, 50)
    out["release_version"] = rv.decode() if isinstance(rv, bytes) else rv
    br_rp = _first(basic, 15)
    out["br_points"] = br_rp
    out["br_rank"] = _tier_name(br_rp, BR_TIERS) if br_rp is not None else None
    cs_pts = _first(basic, 31)
    out["cs_points"] = cs_pts
    out["cs_rank"] = _tier_name(cs_pts, CS_TIERS) if cs_pts is not None else None
    # guild = top-level field 6 (ClanInfoBasic)
    guild = _first(top, 6)
    out["guild"] = None
    if isinstance(guild, bytes) and guild:
        g = _pb_walk(guild)
        gname = _first(g, 2)
        if isinstance(gname, bytes):
            try:
                gname = gname.decode("utf-8")
            except Exception:
                gname = "?"
        out["guild"] = {
            "id": _first(g, 1), "name": gname, "level": _first(g, 4),
            "capacity": _first(g, 5), "members": _first(g, 6),
        } if _first(g, 1) else None
    # pet = top-level field 8
    pet = _first(top, 8)
    out["pet"] = None
    if isinstance(pet, bytes) and pet:
        pt = _pb_walk(pet)
        pname = _first(pt, 2)
        if isinstance(pname, bytes):
            try:
                pname = pname.decode("utf-8")
            except Exception:
                pname = None
        if pname:
            out["pet"] = {"name": pname, "level": _first(pt, 3)}
    # credit score = top-level field 11
    cs = _first(top, 11)
    if isinstance(cs, bytes) and cs:
        c = _pb_walk(cs)
        out["credit_score"] = _first(c, 1)
    # signature = top-level field 9 -> field 9
    soc = _first(top, 9)
    if isinstance(soc, bytes) and soc:
        s = _pb_walk(soc)
        sig = _first(s, 9)
        if isinstance(sig, bytes) and sig:
            try:
                out["signature"] = sig.decode("utf-8")[:80]
            except Exception:
                pass
    return out


DEAD_FILE = Path(__file__).parent / "dead_tokens.json"
HEALTH_TARGET = 9563969432  # known-valid popular UID for auth checks


def load_dead() -> set:
    try:
        return set(json.loads(DEAD_FILE.read_text()))
    except Exception:
        return set()


def add_dead(account_ids) -> int:
    dead = load_dead()
    for a in account_ids:
        if a:
            dead.add(a)
    DEAD_FILE.write_text(json.dumps(sorted(str(d) for d in dead)))
    return len(dead)


def dead_count() -> int:
    return len(load_dead())


def check_token(jwt: str) -> bool:
    """Lightweight liveness probe: a stats call returning HTTP 200 = token alive."""
    try:
        wire = _pb(1, HEALTH_TARGET) + _pb(2, 1)
        h = dict(_headers(jwt))
        h["ReleaseVersion"] = "OB53"
        h["X-GA-SV"] = "1789556611"
        r = httpx.post(PROFILE_URL, headers=h, data=_enc(wire), timeout=15, verify=False)
        return r.status_code == 200
    except Exception:
        return False


def drop_from_pool(dead_ids) -> int:
    """Remove dead account_ids from the cached pool file (minted part)."""
    try:
        pool = json.loads(POOL_FILE.read_text())
    except Exception:
        return 0
    dead = set(dead_ids)
    kept = [t for t in pool if t.get("account_id") not in dead]
    if len(kept) != len(pool):
        POOL_FILE.write_text(json.dumps(kept, indent=1))
    return len(pool) - len(kept)




# ── Guild (clan) API ─────────────────────────────────────────
CLAN_INFO_URL = "https://clientbp.ggpolarbear.com/GetClanInfoByClanID"
CLAN_MAIN_URL = "https://clientbp.ggpolarbear.com/GetClanMainPageInfo"


def _pb_varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _walk_pb(buf: bytes):
    """Generic single-pass protobuf walk: returns list of (field, kind, value)."""
    res = []
    p = 0
    n = len(buf)

    def rv(p):
        v = s = 0
        while p < n:
            b = buf[p]
            p += 1
            v |= (b & 0x7F) << s
            if not b & 0x80:
                return v, p
            s += 7
        return 0, p

    while p < n:
        tag, p = rv(p)
        f, wt = tag >> 3, tag & 7
        if wt == 0:
            v, p = rv(p)
            res.append((f, "int", v))
        elif wt == 2:
            ln, p = rv(p)
            res.append((f, "bytes", buf[p:p + ln]))
            p += ln
        elif wt == 5:
            res.append((f, "f32", buf[p:p + 4]))
            p += 4
        elif wt == 1:
            res.append((f, "f64", buf[p:p + 8]))
            p += 8
        else:
            break
    return res


def get_guild(clan_id: int, jwt: str = None) -> dict | None:
    """Guild basic info + active member UIDs (captain, officers, recent actives)."""
    import base64 as _b64
    if not jwt:
        pool = get_pool()
        if not pool:
            return None
        jwt = pool[0]["token"]
    body = _enc(b"\x08" + _pb_varint(int(clan_id)) + b"\x10\x01")

    # 1) basic info (plaintext protobuf response)
    try:
        r = httpx.post(CLAN_INFO_URL, headers=_headers(jwt, "OB55"), data=body, timeout=20, verify=False)
        if r.status_code != 200 or not r.content:
            return None
        pt = r.content
    except Exception:
        return None

    out = {"clan_id": int(clan_id), "uids": [], "officers": []}
    basic = None
    for f, t, v in _walk_pb(pt):
        if f == 1 and basic is None:
            out["clan_id"] = v
        elif f == 2 and t == "bytes":
            try:
                inner = [sv for sf, st, sv in _walk_pb(v) if sf == 2 and st == "bytes"]
                if inner:
                    out["name"] = inner[0].decode("utf-8", "replace")
                else:
                    out["name"] = v.decode("utf-8", "replace")
            except Exception:
                out["name"] = v.decode("utf-8", "replace")
        elif f == 4:
            out["captain"] = v
        elif f == 5:
            out["level"] = v
        elif f == 7:
            out["members_total"] = v
        elif f == 6:
            out["capacity"] = v
        elif f == 13 and t == "bytes":
            out["region"] = v.decode("utf-8", "replace")
        elif f == 15 and t == "bytes":
            import json as _json
            try:
                out["officers"] = _json.loads(v.decode("utf-8"))
            except Exception:
                pass

    # 2) main page activity (plaintext protobuf) -> active member UIDs
    uids = set()
    if out.get("captain"):
        uids.add(out["captain"])
    uids.update(out.get("officers") or [])
    try:
        r2 = httpx.post(CLAN_MAIN_URL, headers=_headers(jwt, "OB55"), data=body, timeout=20, verify=False)
        if r2.status_code == 200 and r2.content:
            for f, t, v in _walk_pb(r2.content):
                if f == 3 and t == "bytes":
                    for sf, st, sv in _walk_pb(v):
                        if sf == 1 and st == "int":
                            uids.add(sv)
    except Exception:
        pass
    out["uids"] = sorted(uids)
    return out

GLORY_POINTS_FIELDS = (20, 36, 37, 39, 41)  # points/exp-like counters in ClanInfo


def get_clan_stats(clan_id: int, jwt: str = None) -> dict | None:
    """Glory dashboard data: level, members, exp/points counters, active members."""
    if not jwt:
        pool = get_pool()
        if not pool:
            return None
        jwt = pool[0]["token"]
    body = _enc(b"\x08" + _pb_varint(int(clan_id)) + b"\x10\x01")
    try:
        r = httpx.post(CLAN_INFO_URL, headers=_headers(jwt, "OB55"), data=body, timeout=20, verify=False)
        if r.status_code != 200 or not r.content:
            return None
    except Exception:
        return None
    out = {"clan_id": int(clan_id)}
    for f, t, v in _walk_pb(r.content):
        if f == 2 and t == "bytes":
            try:
                inner = [sv for sf, st, sv in _walk_pb(v) if sf == 2 and st == "bytes"]
                out["name"] = (inner[0] if inner else v).decode("utf-8", "replace")
            except Exception:
                out["name"] = v.decode("utf-8", "replace")
        elif f == 3:
            out["created"] = v
        elif f == 4:
            out["captain"] = v
        elif f == 5:
            out["level"] = v
        elif f == 6:
            out["capacity"] = v
        elif f == 7:
            out["members"] = v
        elif f == 12 and t == "bytes":
            out["notice"] = v.decode("utf-8", "replace")
        elif f == 13 and t == "bytes":
            out["region"] = v.decode("utf-8", "replace")
        elif f in GLORY_POINTS_FIELDS:
            out[f"p{f}"] = v
    # active members with levels + last seen
    members = []
    try:
        r2 = httpx.post(CLAN_MAIN_URL, headers=_headers(jwt, "OB55"), data=body, timeout=20, verify=False)
        if r2.status_code == 200 and r2.content:
            for f, t, v in _walk_pb(r2.content):
                if f == 3 and t == "bytes":
                    m = {}
                    for sf, st, sv in _walk_pb(v):
                        if sf == 1: m["uid"] = sv
                        elif sf == 2: m["level"] = sv
                        elif sf == 3: m["state"] = sv
                        elif sf == 4: m["last"] = sv
                    if m.get("uid"):
                        members.append(m)
                elif f == 4:
                    out["next_reset"] = v
    except Exception:
        pass
    out["active"] = members
    return out


def request_join_clan(clan_id: int, jwt: str) -> tuple[bool, str]:
    """Send a guild join request. f1-only body + Bearer (VERIFIED WORKING 2026-10-25)."""
    try:
        h = dict(_headers(jwt, "OB55"))
        h["Authorization"] = f"Bearer {jwt}"
        h["Content-Type"] = "application/octet-stream"
        h["X-Unity-Version"] = "2018.4.11f1"
        h.pop("X-GA-SV", None)
        body = _enc(b"\x08" + _pb_varint(int(clan_id)))
        r = httpx.post("https://clientbp.ggpolarbear.com/RequestJoinClan",
                       headers=h, data=body, timeout=15, verify=False)
        msg = r.content.decode("utf-8", "replace").strip()
        return r.status_code == 200, msg or "OK"
    except Exception as e:
        return False, f"ERR:{e}"


def fetch_external_tokens() -> list:
    """Pull fresh JWTs from public token pools (live community bots)."""
    now = time.time()
    out = []
    for url in EXTERNAL_TOKEN_URLS:
        try:
            r = httpx.get(url, timeout=20, verify=False)
            if r.status_code != 200:
                continue
            d = r.json()
            items = d if isinstance(d, list) else [d]
            for it in items:
                tok = it.get("token") if isinstance(it, dict) else it
                if not isinstance(tok, str) or not tok.startswith("eyJ"):
                    continue
                try:
                    pl = tok.split(".")[1]
                    pl += "=" * (-len(pl) % 4)
                    c = json.loads(base64.urlsafe_b64decode(pl))
                except Exception:
                    continue
                if c.get("exp", 0) < now + 300:
                    continue
                if c.get("lock_region") == "IND":
                    continue
                out.append({"uid": str(c.get("external_uid") or c.get("account_id")),
                            "pass": "", "token": tok,
                            "account_id": c.get("account_id"),
                            "region": c.get("lock_region", "BD"),
                            "exp": c.get("exp"), "external": True})
        except Exception:
            continue
    seen, uniq = set(), []
    for t in out:
        if t["account_id"] and t["account_id"] not in seen:
            seen.add(t["account_id"])
            uniq.append(t)
    return uniq


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
        base = refresh_pool()
    else:
        pool = json.loads(POOL_FILE.read_text())
        now = time.time()
        if not pool or all(t.get("exp", 0) < now + 60 for t in pool):
            base = refresh_pool()
        else:
            base = [t for t in pool if t.get("exp", 0) > now + 60]
    try:
        ext = fetch_external_tokens()
    except Exception:
        ext = []
    seen = {t.get("account_id") for t in base}
    merged = base + [t for t in ext if t["account_id"] not in seen]
    dead = load_dead()
    if dead:
        merged = [t for t in merged if str(t.get("account_id")) not in dead]
    return merged


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
