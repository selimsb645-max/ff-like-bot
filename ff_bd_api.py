#!/usr/bin/env python3
"""
FF BD API — stats + likes via JWT pool.
Endpoints verified 2026-10-23:
  GetPlayerPersonalShow / LikeProfile @ the BD client host (OB53/55, Bearer JWT)
  Host is resolved at runtime (garena_hosts): ggpolarbear.com went NXDOMAIN on
  2026-11-03 and every hardcoded call to it silently returned "no profile".
  JWT pool: external minting service (uid+pass -> fresh JWT per request)
Target uid = BD player's account_id (11-digit, as shown in profile).
"""
import os, httpx, json, time, re, warnings, binascii, base64
from pathlib import Path
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

warnings.filterwarnings("ignore")
AES_KEY = b"Yg&tc%DEuh6%Zc^8"
AES_IV = b"6oyZDr22E3ychjM%"
import garena_hosts as _hosts


def _proxy() -> str:
    """BD exit proxy for every Garena call, when one is configured.

    Order matters: a like-specific override wins, then the phone tunnel that
    run.sh discovers, then the shared BD proxy the glory engine uses.
    A malformed value (socks5://:1080 with no host) is ignored, not used.
    """
    for key in ("LIKE_PROXY_URL", "PHONE_PROXY_URL", "BD_PROXY_URL"):
        val = (os.environ.get(key) or "").strip()
        if not val or "://" not in val:
            continue
        if not val.split("://", 1)[1].split("/", 1)[0].strip(":"):
            continue
        return val
    return ""


_PROXY_STATE = {"url": None, "ok": False, "checked": 0.0}
_PROXY_TTL = 600  # re-check a proxy every 10 minutes


def _proxy_healthy(px: str) -> bool:
    """A dead BD proxy must never hang every call - check it once, cache result."""
    now = time.time()
    if _PROXY_STATE["url"] == px and now - _PROXY_STATE["checked"] < _PROXY_TTL:
        return _PROXY_STATE["ok"]
    ok = False
    try:
        r = httpx.get("https://ipinfo.io/json", proxy=px, timeout=7)
        d = r.json() if r.status_code == 200 else {}
        ok = d.get("country") == "BD"
        print(f"[proxy] health {px[:40]} -> {d.get('ip')} {d.get('country')} {'OK' if ok else 'BAD'}")
    except Exception as e:
        print(f"[proxy] health {px[:40]} FAILED: {str(e)[:60]}")
        ok = False
    _PROXY_STATE.update({"url": px, "ok": ok, "checked": now})
    return ok


def _pxkw() -> dict:
    px = _proxy()
    if not px:
        return {}
    if not _proxy_healthy(px):
        print("[proxy] unhealthy - falling back to DIRECT connection")
        return {}
    return {"proxy": px}


def profile_url() -> str:
    return _hosts.client_base() + "/GetPlayerPersonalShow"


def like_url() -> str:
    return _hosts.client_base() + "/LikeProfile"


def _api_base(token: dict | None = None) -> str:
    """Prefer the server_url the game handed this account at login."""
    own = _hosts.normalise_server_url((token or {}).get("server_url"))
    return own or _hosts.client_base()
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
        # Garena signs with the caller's clock: a stale or missing X-GA-SV is
        # rejected ("SignError1" / "signature is invalid") even with a good JWT
        "X-GA-SV": str(int(time.time())),
        "ReleaseVersion": rv,
    }


def load_creds() -> list:
    if not CREDS_FILE.exists():
        creds = []
    else:
        creds = []
        for line in CREDS_FILE.read_text().splitlines():
            line = line.strip()
            if line and ":" in line and not line.startswith("#"):
                uid, pw = line.split(":", 1)
                creds.append((uid.strip(), pw.strip()))
    # auto-merge phone-born BD accounts (pushed by ff_bd_reg.py v6 via ffPhoneRegisterReport)
    known = {u for u, _ in creds}
    pa = Path(__file__).parent / "phone_accounts.json"
    try:
        for r in json.loads(pa.read_text()):
            u, p = str(r.get("uid", "")).strip(), str(r.get("pw", "")).strip()
            if u and p and u not in known:
                creds.append((u, p))
                known.add(u)
    except Exception:
        pass
    return creds


# ── Self-minted JWT (MajorLogin, verified 2026-10-23) ──────────────
GRANT_URL = "https://100067.connect.garena.com/api/v2/oauth/guest/token:grant"
def major_login_url() -> str:
    return _hosts.login_base() + "/MajorLogin"
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
            timeout=15, verify=False, **_pxkw())
        if r.status_code != 200:
            return None
        d = r.json().get("data", r.json())
        at, oid = d.get("access_token"), d.get("open_id")
        return (at, oid) if at and oid else None
    except Exception:
        return None


def _major_login_bytes(access_token: str, open_id: str):
    """Raw MajorLogin response, or None. The response carries both the JWT and
    the server_url that says WHICH REGION this account belongs to."""
    try:
        wire = (_pf_str(22, open_id) + _pf_str(23, "4")
                + _pf_str(29, access_token) + _pf_str(99, "4"))
        body = _enc(wire)
        h = {"User-Agent": LOGIN_UA, "Connection": "Keep-Alive", "Accept-Encoding": "gzip",
             "X-GA-SV": str(int(time.time())), "Content-Type": "application/x-www-form-urlencoded",
             "Expect": "100-continue", "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1",
             "ReleaseVersion": "OB55"}
        r = httpx.post(major_login_url(), headers=h, data=body, timeout=20,
                       verify=False, **_pxkw())
        if r.status_code != 200:
            return None
        return r.content
    except Exception:
        return None


_JWT_RE = re.compile(rb"(eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]{43})")
_URL_RE = re.compile(rb"https://[A-Za-z0-9._\-]+")


def major_login_full(access_token: str, open_id: str) -> dict | None:
    """{token, server_url} from one MajorLogin, or None."""
    raw = _major_login_bytes(access_token, open_id)
    if not raw:
        return None
    m = _JWT_RE.search(raw)
    if not m:
        return None
    out = {"token": m.group(1).decode(), "server_url": ""}
    for cand in _URL_RE.findall(raw):
        url = cand.decode("utf-8", "replace")
        if "freefiremobile.com" in url or "bp." in url:
            out["server_url"] = url
            break
    return out


def major_login(access_token: str, open_id: str) -> str | None:
    """MajorLogin (OB55, current X-GA-SV) -> JWT or None."""
    full = major_login_full(access_token, open_id)
    return full["token"] if full else None


def mint_jwt_self(uid: str, pw: str) -> dict | None:
    """Mint a JWT for a known game account without any external service."""
    tok_pair = guest_grant(uid, pw)
    if not tok_pair:
        return None
    full = major_login_full(*tok_pair)
    if not full:
        return None
    tok = full["token"]
    pl = tok.split(".")[1]
    pl += "=" * (-len(pl) % 4)
    claims = json.loads(base64.urlsafe_b64decode(pl))
    return {"uid": uid, "pass": pw, "token": tok,
            "account_id": claims.get("account_id"),
            "region": claims.get("lock_region", "BD"),
            "server_url": full.get("server_url", ""),
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
        r = httpx.post(profile_url(), headers=h, data=_enc(wire), timeout=20,
                       verify=False, **_pxkw())
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
        try:
            return set(json.loads(DEAD_FILE.read_text()))
        except Exception:
            return set()
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
        r = httpx.post(profile_url(), headers=h, data=_enc(wire), timeout=15,
                       verify=False, **_pxkw())
        return r.status_code == 200
    except Exception:
        return False


def drop_from_pool(dead_ids) -> int:
    """Remove dead account_ids from the cached pool file (minted part)."""
    try:
        try:
            pool = json.loads(POOL_FILE.read_text())
        except Exception:
            pool = []
    except Exception:
        return 0
    dead = set(dead_ids)
    kept = [t for t in pool if t.get("account_id") not in dead]
    if len(kept) != len(pool):
        POOL_FILE.write_text(json.dumps(kept, indent=1))
    return len(pool) - len(kept)




# ── Guild (clan) API ─────────────────────────────────────────
def clan_info_url() -> str:
    return _hosts.client_base() + "/GetClanInfoByClanID"


def clan_main_url() -> str:
    return _hosts.client_base() + "/GetClanMainPageInfo"


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
        r = httpx.post(clan_info_url(), headers=_headers(jwt, "OB55"), data=body,
                   timeout=20, verify=False, **_pxkw())
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
        r2 = httpx.post(clan_main_url(), headers=_headers(jwt, "OB55"), data=body,
                    timeout=20, verify=False, **_pxkw())
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

EXT_CACHE = Path(__file__).parent / "external_tokens_cache.json"


def _gh_auth_headers() -> dict:
    """raw.githubusercontent is rate-limited per IP and Actions runners share IPs,
    so authenticate the fetch with the repo token when one is available."""
    for key in ("GITHUB_USAGE_TOKEN", "GITHUB_TOKEN", "GITHUB_ACCESS_TOKEN", "USAGE_PAT"):
        tok = (os.environ.get(key) or "").strip()
        if tok:
            return {"Authorization": f"Bearer {tok}", "User-Agent": "ff-bot"}
    return {"User-Agent": "ff-bot"}


def fetch_external_tokens() -> list:
    """Pull fresh JWTs from public token pools (live community bots)."""
    now = time.time()
    out = []
    for url in EXTERNAL_TOKEN_URLS:
        try:
            r = httpx.get(url, timeout=20, verify=False, headers=_gh_auth_headers(),
                          **_pxkw())
            if r.status_code != 200:
                print(f"[POOL] external fetch {url[-28:]} -> HTTP {r.status_code}")
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
    if uniq:
        try:
            EXT_CACHE.write_text(json.dumps(uniq))
        except Exception:
            pass
        return uniq
    # nothing fetched (rate limit / outage): reuse the last good cache
    try:
        cached = json.loads(EXT_CACHE.read_text())
        alive = [t for t in cached if (t.get("exp") or 0) > now + 60]
        if alive:
            print(f"[POOL] external fetch failed -> {len(alive)} cached tokens")
            return alive
    except Exception:
        pass
    return uniq


def load_registered_bd():
    """Game-registered BD accounts (game_register.py pipeline output)."""
    out = []
    try:
        p = Path(__file__).parent / "guests_manager" / "registered_pool.json"
        for r in json.loads(p.read_text()):
            if str(r.get("region", "")).upper() == "BD" and r.get("uid") and r.get("pw"):
                out.append((str(r["uid"]), r["pw"]))
    except Exception:
        pass
    return out


BURNED_FILE = Path(__file__).parent / "like_burned.json"
POOL_MINT_BUDGET = int(os.environ.get("POOL_MINT_BUDGET", "40"))


def _burned_load() -> set:
    try:
        return set(str(x) for x in json.loads(BURNED_FILE.read_text()))
    except Exception:
        return set()


def _burned_add(uids):
    try:
        cur = _burned_load()
        cur.update(str(u) for u in uids)
        BURNED_FILE.write_text(json.dumps(sorted(cur), indent=1))
    except Exception:
        pass


def _exit_country() -> str:
    """Country code this machine (or its BD proxy) looks like from outside."""
    try:
        kw = _pxkw()
        r = httpx.get("https://ipinfo.io/json", timeout=12, verify=False, **kw)
        return str(r.json().get("country") or "")
    except Exception:
        return ""


_BLOCK_CC = ""
_BLOCK_TS = 0.0


_LAST_HARD_FAIL = set()
MINT_TRIES = Path(__file__).parent / "mint_attempts.json"
MINT_RETRY_AFTER = 6 * 3600   # a failed account is retried after 6h, not burned forever


def _tries_load() -> dict:
    try:
        return json.loads(MINT_TRIES.read_text())
    except Exception:
        return {}


def _tries_mark(uids, ok=False):
    d = _tries_load()
    now = time.time()
    for u in uids:
        rec = d.get(str(u)) or {}
        rec["last"] = now
        rec["ok"] = bool(ok)
        rec["fails"] = 0 if ok else int(rec.get("fails") or 0) + 1
        d[str(u)] = rec
    try:
        MINT_TRIES.write_text(json.dumps(d))
    except Exception:
        pass


def refresh_pool() -> list:
    """Mint JWTs for the NEWEST unused pool accounts only.

    A guest account can be logged in exactly once, so the old "mint every account
    on every refresh" was doubly wrong: it burned every usable account in the first
    call and re-tried hundreds of dead uids afterwards (slow enough that callers
    timed out and no likes went out at all). Now: newest first, bounded by
    POOL_MINT_BUDGET, and every account touched is recorded in like_burned.json so
    it is never attempted again.
    """
    burned = _burned_load()
    tries = _tries_load()
    now_ts = time.time()
    cands = []
    for uid, pw in load_creds() + load_registered_bd():
        if str(uid) in burned:
            continue
        rec = tries.get(str(uid)) or {}
        if rec and not rec.get("ok") and now_ts - float(rec.get("last") or 0) < MINT_RETRY_AFTER:
            continue
        try:
            n = int(str(uid))
        except Exception:
            n = 0
        cands.append((n, str(uid), pw))
    cands.sort(reverse=True)                       # newest registrations first
    cands = cands[:POOL_MINT_BUDGET]

    # A guest account can be logged in ONCE and Garena stamps its region from the
    # IP that minted it. Minting from a non-BD machine therefore cannot produce a
    # usable BD token, it only burns accounts. Check before spending anything.
    if not _proxy():
        global _BLOCK_CC, _BLOCK_TS
        cc = ""
        if time.time() - _BLOCK_TS < 60:
            cc = _BLOCK_CC
        else:
            cc = _exit_country()
            _BLOCK_CC, _BLOCK_TS = cc, time.time()
        if cc and cc.upper() != "BD":
            print(f"[POOL] note: this machine exits from {cc.upper()}. Existing BD accounts "
                  "keep their region (it is stamped when the account is created), so their "
                  "tokens still work - minting continues.")

    pool, touched, wrong_region = [], [], 0
    for _n, uid, pw in cands:
        touched.append(uid)
        try:
            t = mint_jwt(uid, pw)
        except Exception as e:
            t = None
            if any(k in str(e).lower() for k in ("not_found", "banned", "invalid_pass")):
                _LAST_HARD_FAIL.add(str(uid))
        if not t:
            continue
        url = str(t.get("server_url") or "")
        if url and not _hosts.is_bd_host(url):
            wrong_region += 1
            print(f"[POOL] {uid} came back on {url} - wrong region for BD likes")
            if wrong_region >= 1:
                print("[POOL] stopping mint: every further account would land in the same "
                      "wrong region and waste its one-time login")
                break
            continue
        pool.append(t)
    hard = [u for u in touched if str(u) in _LAST_HARD_FAIL]
    if hard:
        _burned_add(hard)
    _tries_mark([u for u in touched if u not in hard], ok=bool(pool))
    try:
        POOL_FILE.write_text(json.dumps(pool, indent=1))
    except Exception:
        pass
    print(f"[POOL] minted {len(pool)}/{len(touched)} tokens (budget {POOL_MINT_BUDGET})")
    return pool



# ── SPEED: shared keep-alive client + parallel workers ──────────────
import threading as _th
from concurrent.futures import ThreadPoolExecutor as _TPE

_CLIENT = None
_CLIENT_LOCK = _th.Lock()


def _client():
    """One keep-alive HTTP client for all Garena calls (TLS setup reused)."""
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = httpx.Client(
                    verify=False, timeout=12, **_pxkw(),
                    limits=httpx.Limits(max_connections=60, max_keepalive_connections=30),
                    headers={"Connection": "keep-alive"},
                )
    return _CLIENT


_POOL_CACHE = {"ts": 0.0, "data": []}
_POOL_TTL = 300  # 5 min in-memory pool cache (avoids re-fetching 290 tokens per call)

def get_pool(force: bool = False) -> list:
    if not force and _POOL_CACHE["data"] and time.time() - _POOL_CACHE["ts"] < _POOL_TTL:
        return _POOL_CACHE["data"]
    if force or not POOL_FILE.exists():
        base = refresh_pool()
    else:
        try:
            pool = json.loads(POOL_FILE.read_text())
        except Exception:
            pool = []
        now = time.time()
        if not pool or all(t.get("exp", 0) < now + 60 for t in pool):
            base = refresh_pool()
        else:
            base = [t for t in pool if t.get("exp", 0) > now + 60]
    try:
        ext = fetch_external_tokens()
    except Exception:
        ext = []
    # Accounts we created and keep alive ourselves come first: a public token is
    # shared with other bots, so it is worth less than ours at the same age.
    try:
        owned = json.loads((POOL_FILE.parent / "bd_pool_owned.json").read_text())
        owned = [t for t in owned if t.get("exp", 0) > time.time() + 60]
    except Exception:
        owned = []
    seen = {t.get("account_id") for t in owned}
    base = [t for t in base if t.get("account_id") not in seen] + owned
    seen = {t.get("account_id") for t in base}
    merged = base + [t for t in ext if t["account_id"] not in seen]
    dead = load_dead()
    if dead:
        merged = [t for t in merged if str(t.get("account_id")) not in dead]
    _POOL_CACHE["ts"] = time.time()
    _POOL_CACHE["data"] = merged
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
    r = httpx.post(profile_url(), headers=_headers(jwt, "OB53"), data=payload,
                   timeout=25, verify=False, **_pxkw())
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


READY_FILE = Path(__file__).parent / "like_ready.json"


def is_activated(t: dict, cache: dict) -> bool:
    """A like only counts if the sender has a live game profile (entered game once).
    Freshly registered-but-never-played accounts return 200 but land nothing."""
    uid = str(t.get("uid"))
    if not isinstance(cache, dict):
        cache = {}
    ent = cache.get(uid) or {}
    if ent.get("ready") and time.time() - ent.get("ts", 0) < 6 * 3600:
        return True
    if ent.get("ready") is False and time.time() - ent.get("ts", 0) < 3600:
        return False
    s = get_stats(int(t["account_id"]), t["token"])
    ready = bool(isinstance(s, dict) and s.get("likes") is not None)
    cache[uid] = {"ready": ready, "ts": time.time()}
    return ready


def ready_uids() -> dict:
    """{uid: {ready, ts}} for admin display."""
    try:
        data = json.loads(READY_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def phone_accounts() -> list:
    """Phone-born accounts pushed by the Termux pipeline."""
    try:
        try:
            return json.loads((Path(__file__).parent / "phone_accounts.json").read_text())
        except Exception:
            return []
    except Exception:
        return []


def _filter_activated(pool, cache, workers=32):
    """Check activation in PARALLEL (was the main slowdown: 1 profile call each)."""
    def chk(t):
        try:
            return t if is_activated(t, cache) else None
        except Exception:
            return None
    if not pool:
        return []
    with _TPE(max_workers=min(workers, len(pool))) as ex:
        return [t for t in ex.map(chk, pool) if t]


LAST_LIKE_STATS = {"attempted": 0, "sent": 0, "reasons": {}, "accounts_tried": 0}


def send_like(account_id: int, count: int = 1, used: set = None, workers=24) -> int:
    """Send likes to a target - PARALLEL version (fast).
    Only ACTIVATED accounts (with a live game profile) are used.

    Refuses to fire unless the egress really is Bangladesh. Garena answers 200 for a
    like that comes from the wrong region and silently drops it, so sending anyway
    produces the worst possible outcome: a confident "requests accepted" report and a
    counter that never moves.
    """
    import like_pb2 as lpb
    import random as _rnd
    cc = _exit_country()
    LAST_LIKE_STATS.update({"attempted": 0, "sent": 0, "reasons": {}, "accounts_tried": 0,
                            "blocked": "" if cc == "BD" else
                            f"exit country is {cc or 'unknown'}, not BD"})
    if cc != "BD":
        print(f"[like] blocked: {LAST_LIKE_STATS['blocked']}")
        return 0
    LAST_LIKE_STATS.pop("blocked", None)
    cache = ready_uids()
    raw_pool = get_pool()
    pool = _filter_activated(raw_pool, cache)
    if not pool and raw_pool:
        # activation is only a heuristic - never block a like run on it
        print(f"[like] activation filter found none of {len(raw_pool)}; trying them anyway")
        pool = raw_pool[:150]
    if not pool:
        LAST_LIKE_STATS.update({"attempted": 0, "sent": 0, "accounts_tried": 0,
                                "reasons": {"pool_empty": len(raw_pool)}})
        print(f"[like] pool has no usable account (raw pool {len(raw_pool)})")
        return 0
    try:
        READY_FILE.write_text(json.dumps(cache, indent=1))
    except Exception:
        pass
    used = used if used is not None else set()
    todo = []
    for t in pool:
        key = (t["account_id"], account_id)
        if key in used:
            continue
        todo.append((t, key))
        if len(todo) >= max(count * 2, count + 10):
            break
    if not todo:
        LAST_LIKE_STATS.update({"attempted": 0, "sent": 0, "accounts_tried": 0,
                                "reasons": {"all_pairs_used": 1}})
        print("[like] nothing to do: every pair already used for this target")
        return 0
    m = lpb.like()
    m.uid = int(account_id)
    m.region = "BD"
    payload = _enc(m.SerializeToString())
    cli = _client()
    done = {"sent": 0}
    reasons = {}
    lock = _th.Lock()

    def fire(item):
        t, key = item
        with lock:
            if done["sent"] >= count:
                return
        try:
            time.sleep(_rnd.uniform(0, 0.3))  # jitter: avoid burst signature
            base = _api_base(t)
            r = cli.post(base + "/LikeProfile", headers=_headers(t["token"], "OB55"),
                         data=payload)
            if r.status_code == 200:
                with lock:
                    used.add(key)
                    done["sent"] += 1
                    # LikeProfile answers 200 even when it refuses, so keep the
                    # body: without it "sent" is a guess, not proof.
                    tag = r.content[:24].hex() or "empty"
                    reasons["200:" + tag] = reasons.get("200:" + tag, 0) + 1
            else:
                body = r.content[:60].decode("utf-8", "replace").strip() or f"HTTP {r.status_code}"
                with lock:
                    reasons[body] = reasons.get(body, 0) + 1
        except Exception as e:
            with lock:
                nm = f"network: {type(e).__name__}"
                reasons[nm] = reasons.get(nm, 0) + 1

    with _TPE(max_workers=min(workers, len(todo))) as ex:
        list(ex.map(fire, todo))
    LAST_LIKE_STATS.update({"attempted": len(todo), "sent": done["sent"],
                            "reasons": dict(reasons),
                            "accounts_tried": len({t["account_id"] for t, _ in todo})})
    print(f"[like] attempted={len(todo)} sent={done['sent']} reasons={reasons}")
    return done["sent"]


if __name__ == "__main__":
    pool = refresh_pool()
    print(f"pool: {len(pool)} BD accounts")
    for t in pool[:5]:
        s = get_stats(t["account_id"], t["token"])
        print(f"  {t['account_id']} lvl={s.get('level')} likes={s.get('likes')} name={s.get('name')}")


def get_full_profile(account_id: int, jwt: str = None) -> dict:
    """Public profile: name, level, exp, likes (GetPlayerPersonalShow). Read-only."""
    if not jwt:
        pool = get_pool()
        jwt = pool[0]["token"] if pool else None
    if not jwt:
        return {}
    try:
        body = _enc(b"\x08" + _pb_varint(int(account_id)))
        r = httpx.post(_api_base() + "/GetPlayerPersonalShow",
                       headers=_headers(jwt, "OB55"), data=body, timeout=20,
                       verify=False, **_pxkw())
        if r.status_code != 200:
            return {}
        out = {}
        for f, t, v in _walk_pb(r.content):
            if t == "bytes" and len(v) > 4:
                for sf, st, sv in _walk_pb(v):
                    if sf in (6, 7, 21) and st == "int":
                        out[sf] = sv
        return {"level": out.get(6), "exp": out.get(7), "likes": out.get(21)}
    except Exception:
        return {}

# ── Guild (clan) join ────────────────────────────────────────────────
# Verified against the UNFAIR glory bot: endpoint is /RequestJoinClan and the
# body is an AES-CBC encrypted `MyMessage{field_1: clan_id}` (octet-stream),
# NOT a wire proto. All the /JoinClan,/ApplyClan style paths return 404.
def clan_join_url() -> str:
    return _hosts.client_base() + "/RequestJoinClan"
CLAN_KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
CLAN_IV = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])


def _clan_payload(clan_id: int) -> bytes:
    """MyMessage { field_1: int64 clan_id } -> AES-CBC + PKCS7."""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    val, out = int(clan_id), b""
    while True:
        b7 = val & 0x7F
        val >>= 7
        if val:
            out += bytes([b7 | 0x80])
        else:
            out += bytes([b7])
            break
    return AES.new(CLAN_KEY, AES.MODE_CBC, CLAN_IV).encrypt(pad(b"\x08" + out, 16))


def request_join_clan(clan_id: int, jwt: str = None, verbose: bool = False):
    """Ask the guild to accept this account. Returns (ok, message)."""
    try:
        r = httpx.post(CLAN_JOIN_URL,
                       headers={"Authorization": f"Bearer {jwt}",
                                "X-Unity-Version": "2018.4.11f1", "X-GA": "v1 1",
                                "ReleaseVersion": "OB55",
                                "Content-Type": "application/octet-stream",
                                "Connection": "Keep-Alive", "Accept-Encoding": "gzip"},
                       data=_clan_payload(clan_id), timeout=20, verify=False)
        ok = r.status_code == 200
        msg = f"RequestJoinClan -> {r.status_code}"
        if verbose or not ok:
            msg += f" len={len(r.content)} body={r.content[:60]!r}"
        return (ok, msg)
    except Exception as e:
        return (False, f"RequestJoinClan EXC {str(e)[:80]}")
