#!/usr/bin/env python3
"""Full-device MajorLogin — the wire the UNFAIR build uses (fields 3..107).

Our minimal wire (22/23/29/99) is answered with Protection Bypass on anything that
is not a brand-new registration. This payload reproduces the client fingerprint the
server checks, and it is the only login that has been observed to return the long
(819/815-char) JWT with a usable server_url for older accounts.
"""
import json
import random
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ff_majorlogin as mlm
import ff_bd_api as api
from guests_manager import game_register as gr

DEVICES = Path(__file__).parent / "guests_manager" / "devices.json"
_DEV_CACHE = {}


def _load_devices() -> dict:
    try:
        return json.loads(DEVICES.read_text())
    except Exception:
        return {}


def _save_devices(table: dict) -> None:
    try:
        DEVICES.parent.mkdir(parents=True, exist_ok=True)
        DEVICES.write_text(json.dumps(table, indent=1))
    except Exception:
        pass
import httpx


def _post(url: str, headers: dict, data: bytes, timeout: int = 25):
    """POST with trust_env so HTTPS_PROXY (the phone tunnel) is honoured.

    Kept local instead of reaching into ff_bd_api: the repo gets pulled by several
    runners and a helper that exists today can be gone tomorrow, which is exactly how
    the generator silently stopped logging in.
    """
    return httpx.post(url, headers=headers, content=data, timeout=timeout,
                      verify=False, trust_env=True)

FALLBACK_DEVS = [
    ("Samsung", "SM-G998B", "Adreno (TM) 660", "Android OS 12 / API-31", 1440, 3200, 480),
    ("Xiaomi", "M2007J20CG", "Adreno (TM) 618", "Android OS 12 / API-31", 1080, 2340, 395),
    ("realme", "RMX3241", "Mali-G57 MC2", "Android OS 12 / API-31", 1080, 2400, 405),
]


PUBLIC_IP = ""          # set by guestgen once the exit IP is known


def public_ip() -> str:
    """The IP the server will see. Falls back to empty, never to a fake."""
    global PUBLIC_IP
    if PUBLIC_IP:
        return PUBLIC_IP
    try:
        import urllib.request
        with urllib.request.urlopen("https://api.ipify.org", timeout=8) as r:
            PUBLIC_IP = r.read().decode().strip()
    except Exception:
        pass
    return PUBLIC_IP


def device_for(uid: str) -> dict:
    """One device per account, reused for every request in its whole life.

    The anti-cheat compares the device claimed at registration with the one on
    each later login; a fresh random phone per call is treated as a tampered
    client and banned as "Cheat".
    """
    uid = str(uid)
    if uid in _DEV_CACHE:
        return _DEV_CACHE[uid]
    table = _load_devices()
    if uid in table:
        _DEV_CACHE[uid] = table[uid]
        return table[uid]
    brand, model, gpu, sw, w, h, dpi = random.choice(FALLBACK_DEVS)
    dev = {"unique_device_id": f"Google|{uuid.uuid4()}", "brand": brand, "model": model,
           "gpu_renderer": gpu, "system_software": sw, "screen_width": w,
           "screen_height": h, "screen_dpi": dpi,
           "processor_details": "ARM64 FP ASIMD AES | 2000 | 8", "memory": 5653,
           "client_ip": public_ip()}
    table[uid] = dev
    _DEV_CACHE[uid] = dev
    _save_devices(table)
    return dev


def build_full_payload(open_id: str, access_token: str, dev: dict,
                       platform: int = 4, client_version: str = "1.132.11",
                       region: str = "BD") -> bytes:
    p = {
        3: str(datetime.now())[:-7], 4: "free fire", 5: "1",
        7: str(client_version), 8: str(dev.get("system_software", "Android OS 14 / API-34")),
        9: "Handheld", 10: "Telkomsel", 11: "CarrierDataNetwork",
        12: str(dev.get("screen_width", 1639)), 13: str(dev.get("screen_height", 720)),
        14: str(dev.get("screen_dpi", 320)),
        15: str(dev.get("processor_details", "ARM64 FP ASIMD AES | 2000 | 8")),
        16: str(dev.get("memory", 5653)),
        17: str(dev.get("gpu_renderer", "Mali-G52 MC2")),
        18: "OpenGL ES 3.2 v1.r49p1-03bet0",
        19: str(dev.get("unique_device_id", f"Google|{uuid.uuid4()}")),
        20: str(dev.get("client_ip") or public_ip()),
        21: "en", 22: str(open_id), 23: str(platform), 24: "Handheld",
        25: str(dev.get("model", "Xiaomi")), 26: region.upper(), 29: str(access_token),
        30: "1", 41: "Telkomsel", 42: "4G", 57: "7428b253defc164018c604a1ebbfebdf",
        60: "109278", 61: "55402", 62: "868", 64: "55927", 65: "109278", 66: "55927",
        67: "109278", 73: 2,
        74: "/data/app/~~6Cw028g_ld-8Z0z2qhrOLA==/com.dts.freefireth-i3wiaEmpx8CbiRVhUMx_ag==/lib/arm64",
        76: 1,
        77: "b8e0cd5e295eee42f5860d3c86e483dd|/data/app/~~6Cw028g_ld-8Z0z2qhrOLA==/com.dts.freefireth-i3wiaEmpx8CbiRVhUMx_ag==/base.apk",
        78: 6, 79: 1, 81: "32", 83: "2019121229", 85: 3, 86: "OpenGLES2",
        87: 3071, 88: 4, 89: 14466, 93: "3rd_party",
        94: "KqsHTyb7yHjsAiyNWUUZa0DSOD6U0AhDQ76smAo1WeHHHpSmJ8KMRyRrxb2ttgJqO9YOqO/K6AORAvDMAX/qBS8+JahIkrM7kHfzlfULbgRAoZ9r",
        95: "111207", 96: "{\"cur_rate\":[60,90,120],\"support_etc2\":true}", 97: "1",
        98: "1", 99: str(platform), 100: str(platform), 103: "1", 104: "52837", 105: "1",
        106: "https://dl.gmc.freefiremobile.com/live/ABHotUpdates/|211c933168f55902c7dfbfd8c4e2957d",
        107: "1.3fbadbf45f44ce9",
    }
    wire = b"".join(gr.pf(f, v) for f, v in p.items())
    return gr.encrypt_payload(wire)


def full_major_login(uid, open_id, access_token, host=None, release="OB55", dev=None,
                     region="BD"):
    blob = build_full_payload(open_id, access_token, dev or device_for(uid), region=region)
    hosts = [host] if host else ["https://clientbp.ppmainecoonghj.com",
                                 "https://loginbp.ppmainecoonghj.com"]
    h = {"User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
         "Accept-Encoding": "gzip", "X-GA-SV": str(int(time.time())),
         "Content-Type": "application/x-www-form-urlencoded", "X-Unity-Version": "2018.4.12f1",
         "X-GA": "v1 1", "X-GA-RV": release, "ReleaseVersion": release}
    last = None
    for base in hosts:
        for attempt in (1, 2):
            try:
                r = _post(base.rstrip("/") + "/MajorLogin", h, blob, 25)
                if r.status_code != 200:
                    last = f"HTTP {r.status_code} {r.content[:40]!r}"
                    continue
                info = mlm._parse_any(r.content)
                if info.get("jwt"):
                    # field 10 is the account's OWN game host. Keep it apart from
                    # the host we dialled, so "no server_url" stays visible and the
                    # caller can run ChooseRegion instead of silently guessing.
                    if not info.get("server_url"):
                        info["server_url"] = info.get("url") or ""
                    info["url"] = base
                    info["account_id"] = info.get("account_uid") or int(uid)
                    info.setdefault("ts", int(time.time()))
                    return info
                last = f"no jwt in {len(r.content)}B"
            except Exception as ex:
                last = f"{type(ex).__name__}: {ex}"
            time.sleep(2)
    print("   login failed:", last, flush=True)
    return None



def choose_region(jwt: str, host: str, region: str = "BD", release: str = "OB55"):
    """Lock the account to a region.

    Verified working sequence (UNFAIR, 2026-10-30): MajorLogin -> ChooseRegion ->
    MajorLogin #2. Skipping this step is why freshly minted accounts came back on
    a foreign region, showed "Indonesia" in game and never got their likes
    credited.
    """
    url = host.rstrip("/") + "/ChooseRegion"
    body = gr.encrypt_payload(gr.pf(1, region.upper()))
    h = {"User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
         "Accept-Encoding": "gzip", "X-GA-SV": str(int(time.time())),
         "Content-Type": "application/x-www-form-urlencoded",
         "X-Unity-Version": "2018.4.12f1", "X-GA": "v1 1", "X-GA-RV": release,
         "Authorization": f"Bearer {jwt}", "ReleaseVersion": release}
    try:
        return _post(url, h, body, 20).status_code
    except Exception:
        return None


def login_locked(uid, open_id, access_token, region="BD", release="OB55", host=None):
    """Full login that also guarantees the region lock is in place."""
    host = (host or "https://loginbp.ppmainecoonghj.com").rstrip("/")
    info = full_major_login(uid, open_id, access_token, host=host,
                            release=release, region=region)
    if info and info.get("server_url"):
        return info
    jwt = (info or {}).get("jwt") or ""
    if not jwt:
        return info
    status = choose_region(jwt, host, region, release)
    if status != 200:
        print(f"   ChooseRegion({region}) -> {status}", flush=True)
        return info
    print(f"   ChooseRegion({region}) ok, re-running MajorLogin", flush=True)
    time.sleep(2.0)
    again = full_major_login(uid, open_id, access_token, host=host,
                             release=release, region=region)
    return again or info


if __name__ == "__main__":
    pool = json.loads(Path("bd_candidates_darkmaster.json").read_text())
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    sess = gr.new_session()
    for rec in pool[:n]:
        uid = rec["uid"]
        at, oid = gr.token_grant(sess, uid, rec["pass"])
        print(f"{uid} lvl{rec.get('level')} grant={bool(at)}", flush=True)
        if not at:
            continue
        info = full_major_login(uid, oid, at)
        if not info:
            continue
        print(f"   jwt={len(info['jwt'])} url={info.get('url')} key={len(info.get('key') or b'')}b", flush=True)
        st = api.get_stats(int(uid), info["jwt"])
        print("   stats:", st, flush=True)
