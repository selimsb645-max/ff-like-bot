"""Modern OB55 MajorLogin with the FULL device payload.

Why this exists (2026-10-28): the old minimal MajorLogin wire (fields 22/23/29/99)
now gets answered with an encrypted "Protection Bypass" blob instead of a JWT.
Garena's gate validates the full device/game-security payload, so we now send the
complete MajorLogin message (client_version 1.132.3, device fingerprint, storage,
library token, analytics extras...) AES-CBC encrypted with the fixed client key,
and parse the plain MajorLoginRes protobuf that comes back.

Recipe cross-checked against a public working guild bot (subhajit-maji) on
2026-10-28; the field numbers below match MajorLoginReq.proto / MajorLoginRes.proto.
"""
import hashlib
import time
import datetime

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

ML_URL = "https://loginbp.ppmainecoonghj.com/MajorLogin"
AES_KEY = b"Yg&tc%DEuh6%Zc^8"
AES_IV = b"6oyZDr22E3ychjM%"
# 2026-10-28: X-GA-SV must be the CURRENT unix timestamp (verified against a live
# implementation). A stale fixed value makes Garena answer 400 SignError1 /
# encrypted "Protection Bypass" no matter how complete the payload is.
def headers() -> dict:
    return {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; ASUS_Z01QD Build/PI)",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "X-GA-SV": str(int(time.time())),
        "ReleaseVersion": "OB55",
        "Authorization": "Bearer",
        "Content-Type": "application/octet-stream",
    }


HEADERS = headers()  # kept for backwards compatibility


# ---------- protobuf wire helpers ----------
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _f_int(field: int, value: int) -> bytes:
    return _varint((field << 3) | 0) + _varint(int(value))


def _f_bytes(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _f_str(field: int, value: str) -> bytes:
    return _f_bytes(field, str(value).encode())


def _device_id(uid) -> str:
    """Stable per-account device fingerprint (same account = same device, avoids
    'new device' protection triggers on every login)."""
    h = hashlib.md5(f"ff-device-{uid}".encode()).hexdigest()
    return f"Google|{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def build_payload(uid, open_id: str, access_token: str, client_ip: str = "223.191.51.89") -> bytes:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    p = b""
    p += _f_str(3, now)                      # event_time
    p += _f_str(4, "free fire")              # game_name
    p += _f_int(5, 1)                        # platform_id
    p += _f_str(7, "1.132.3")                # client_version
    p += _f_str(8, "Android OS 9 / API-28 (PQ3B.190801.10101846/G9650ZHU2ARC6)")
    p += _f_str(9, "Handheld")               # system_hardware
    p += _f_str(10, "Verizon")               # telecom_operator
    p += _f_str(11, "WIFI")                  # network_type
    p += _f_int(12, 1920)                    # screen_width
    p += _f_int(13, 1080)                    # screen_height
    p += _f_str(14, "280")                   # screen_dpi
    p += _f_str(15, "ARM64 FP ASIMD AES VMH | 2865 | 4")
    p += _f_int(16, 3003)                    # memory
    p += _f_str(17, "Adreno (TM) 640")       # gpu_renderer
    p += _f_str(18, "OpenGL ES 3.1 v1.46")   # gpu_version
    p += _f_str(19, _device_id(uid))         # unique_device_id
    p += _f_str(20, client_ip)               # client_ip
    p += _f_str(21, "en")                    # language
    p += _f_str(22, open_id)
    p += _f_str(23, "4")                     # open_id_type
    p += _f_str(24, "Handheld")              # device_type
    sec = _f_int(6, 55) + _f_int(8, 81)      # GameSecurity{version:55, hidden_value:81}
    p += _f_bytes(25, sec)                   # memory_available
    p += _f_str(29, access_token)
    p += _f_int(30, 1)                       # platform_sdk_id
    p += _f_str(41, "Verizon")               # network_operator_a
    p += _f_str(42, "WIFI")                  # network_type_a
    p += _f_str(57, "7428b253defc164018c604a1ebbfebdf")
    p += _f_int(60, 36235)
    p += _f_int(61, 31335)
    p += _f_int(62, 2519)
    p += _f_int(63, 703)
    p += _f_int(64, 25010)
    p += _f_int(65, 26628)
    p += _f_int(66, 32992)
    p += _f_int(67, 36235)
    p += _f_int(73, 3)                       # login_by
    p += _f_str(74, "/data/app/com.dts.freefireth-YPKM8jHEwAJlhpmhDhv5MQ==/lib/arm64")
    p += _f_int(76, 1)                       # reg_avatar
    p += _f_str(77, "5b892aaabd688e571f688053118a162b|/data/app/com.dts.freefireth-YPKM8jHEwAJlhpmhDhv5MQ==/base.apk")
    p += _f_int(78, 3)                       # channel_type
    p += _f_int(79, 2)                       # cpu_type
    p += _f_str(81, "64")                    # cpu_architecture
    p += _f_str(83, "2019118695")            # client_version_code
    p += _f_str(86, "OpenGLES2")             # graphics_api
    p += _f_int(87, 16383)                   # supported_astc_bitset
    p += _f_int(88, 4)                       # login_open_id_type
    import base64 as _b64
    p += _f_bytes(89, _b64.b64decode("FwQVTgUPX1UaUllDDwcWCRBpWA0FUgsvA1snWlBaO1kFYg=="))
    p += _f_int(92, 13564)                   # loading_time
    p += _f_str(93, "android")               # release_channel
    p += _f_str(94, "KqsHTymw5/5GB23YGniUYN2/q47GATrq7eFeRatf0NkwLKEMQ0PK5BKEk72dPflAxUlEBir6Vtey83XqF593qsl8hwY=")
    p += _f_int(95, 110009)                  # android_engine_init_flag
    p += _f_int(97, 1)                       # if_push
    p += _f_int(98, 1)                       # is_vpn
    p += _f_str(99, "4")                     # origin_platform_type
    p += _f_str(100, "4")                    # primary_platform_type
    return p


def encrypt(payload: bytes) -> bytes:
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(payload, AES.block_size))


def _walk(buf: bytes):
    """Minimal protobuf reader -> list of (field, wire_type, value)."""
    out, i, n = [], 0, len(buf)
    while i < n:
        key = shift = 0
        while True:
            b = buf[i]; i += 1
            key |= (b & 0x7F) << shift; shift += 7
            if not b & 0x80:
                break
        field, wt = key >> 3, key & 7
        if wt == 0:
            v = shift = 0
            while True:
                b = buf[i]; i += 1
                v |= (b & 0x7F) << shift; shift += 7
                if not b & 0x80:
                    break
            out.append((field, wt, v))
        elif wt == 2:
            ln = shift = 0
            while True:
                b = buf[i]; i += 1
                ln |= (b & 0x7F) << shift; shift += 7
                if not b & 0x80:
                    break
            out.append((field, wt, buf[i:i + ln])); i += ln
        elif wt == 5:
            i += 4
        elif wt == 1:
            i += 8
        else:
            break
    return out


def parse_response(buf: bytes) -> dict:
    d = {}
    for f, wt, v in _walk(buf):
        if f == 1 and wt == 0:
            d["account_uid"] = v
        elif f == 2 and wt == 2:
            d["region"] = v.decode("utf-8", "ignore")
        elif f == 8 and wt == 2:
            d["jwt"] = v.decode("utf-8", "ignore")
        elif f == 10 and wt == 2:
            d["url"] = v.decode("utf-8", "ignore")
        elif f == 21 and wt == 0:
            d["ts"] = v
        elif f == 22 and wt == 2:
            d["key"] = v
        elif f == 23 and wt == 2:
            d["iv"] = v
    return d


def build_min_payload(open_id: str, access_token: str) -> bytes:
    """The minimal wire Garena accepts today: 22=open_id, 23="4", 29=token, 99="4"."""
    return (_f_str(22, open_id) + _f_str(23, "4")
            + _f_str(29, access_token) + _f_str(99, "4"))


ML_HOSTS = ("https://loginbp.ggblueshark.com/MajorLogin",
            "https://loginbp.ppmainecoonghj.com/MajorLogin",
            "https://loginbp.ggpolarbear.com/MajorLogin")


def _parse_any(buf: bytes) -> dict:
    """Response starts with a 64-byte non-protobuf block; try both offsets."""
    for blob in (buf, buf[64:] if len(buf) > 64 else b""):
        if not blob:
            continue
        try:
            resp = parse_response(blob)
        except Exception:
            continue
        if resp.get("jwt"):
            return resp
    return {}


def major_login(uid, open_id: str, access_token: str, client_ip: str = "223.191.51.89") -> dict | None:
    """Returns {jwt, key, iv, ts, url, region, account_id} or None.

    Tries the minimal wire first (what a live Oct-2026 implementation uses), then
    the full device payload, over all known login hosts, always with a fresh
    X-GA-SV timestamp.
    """
    payloads = [build_min_payload(open_id, access_token),
                build_payload(uid, open_id, access_token, client_ip)]
    for payload in payloads:
        try:
            body = encrypt(payload)
        except Exception:
            continue
        for host in ML_HOSTS:
            try:
                r = httpx.post(host, data=body, headers=headers(), timeout=25, verify=False)
                if r.status_code != 200:
                    continue
                resp = _parse_any(r.content)
                if not resp.get("jwt"):
                    continue
                return _shape(resp, uid)
            except Exception:
                continue
    return None


def _shape(resp: dict, uid) -> dict:
    return {
            "jwt": resp["jwt"],
            "key": resp.get("key", b"").hex() if isinstance(resp.get("key"), bytes) else resp.get("key"),
            "iv": resp.get("iv", b"").hex() if isinstance(resp.get("iv"), bytes) else resp.get("iv"),
            "ts": resp.get("ts"),
            "url": resp.get("url"),
            "region": resp.get("region", "bd"),
        "account_id": resp.get("account_uid") or int(uid),
    }
