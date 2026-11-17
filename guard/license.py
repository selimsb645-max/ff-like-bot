"""লাইসেন্স যাচাই: সার্ভার ছাড়া চলে না, তবে ২৪ ঘণ্টা অফলাইন সহনশীল।

কেউ ফাইল কপি করে নিলেও:
- কোডটা তার নিজের না হলে সার্ভার প্রথমেই আটকাবে
- কোড থাকলেও ডিভাইস ফিঙ্গারপ্রিন্ট মিলবে না
- সার্ভার বন্ধ/ব্লকড হলে শেষ সফল যাচাই থেকে ২৪ ঘণ্টা পর নিজেই থেমে যাবে
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config
from .device import fingerprint

_last: dict = {}


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def code() -> str:
    if config.CODE:
        return config.CODE
    doc = _load(config.LICENSE_FILE)
    return str(doc.get("code", "")).strip()


def call(action: str, entry: str, extra: dict | None = None) -> dict:
    if not config.GUARD_URL:
        raise RuntimeError(
            "guard URL সেট করা নেই — FF_GUARD_URL দিন বা guard_url.txt লিখুন "
            "(লাইসেন্স ও কী সার্ভার থেকেই আসে, রেপোতে থাকে না)"
        )
    payload = {"action": action, "code": code(), "fingerprint": fingerprint(),
               "entry": entry, "version": config.VERSION}
    if extra:
        payload.update(extra)
    req = urllib.request.Request(
        config.GUARD_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "ff-guard/" + config.VERSION})
    try:
        with urllib.request.urlopen(req, timeout=config.VERIFY_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": f"http_{e.code}", "message": body or str(e.reason)}
    except Exception as e:
        # নেট নেই, DNS নেই, টাইমআউট — এগুলোতে ২৪ ঘণ্টার অফলাইন ছাড় ব্যবহার হবে
        return {"ok": False, "error": "network", "message": f"সার্ভারে পৌঁছানো যায়নি ({type(e).__name__})"}


def verify(entry: str = "", with_secrets: bool = False) -> dict:
    """সার্ভার যাচাই; সফল হলে pending সেভ করে এবং ২৪ ঘণ্টা অফলাইনে টিকে থাকে।"""
    if not code():
        return {"ok": False, "error": "no_license",
                "message": "লাইসেন্স কোড নেই — python3 guard/cli.py login দিয়ে বসান"}
    res = call("verify", entry, {"withSecrets": True} if with_secrets else None)
    if res.get("ok"):
        _save(config.LAST_OK_FILE, {"at": int(time.time()), "watermark": res.get("watermark", ""),
                                    "secrets": res.get("secrets") or {}})
        return res
    last = _load(config.LAST_OK_FILE)
    age_h = (time.time() - float(last.get("at", 0))) / 3600 if last.get("at") else 999
    if res.get("error") in ("wrong_device", "revoked", "expired", "bad_code"):
        return res
    if age_h < config.OFFLINE_GRACE_HOURS:
        return {"ok": True, "offline": True, "watermark": last.get("watermark", ""),
                "secrets": last.get("secrets") or {},
                "message": f"সার্ভারে পৌঁছাইনি, শেষ সফল যাচাই {age_h:.1f} ঘণ্টা আগে"}
    return {"ok": False, "error": "offline_expired",
            "message": f"সার্ভারে পৌঁছানো যাচ্ছে না, আর শেষ যাচাই {age_h:.0f} ঘণ্টা আগে — থামাচ্ছি"}


def require(entry: str = "") -> dict:
    """ব্যর্থ হলে প্রসেস বন্ধ। সফল হলে ওয়াটারমার্ক প্রিন্ট করে।"""
    res = verify(entry)
    if not res.get("ok"):
        msg = res.get("message") or res.get("error") or "লাইসেন্স যাচাই ব্যর্থ"
        print(f"\n⛔ {msg}\n", flush=True)
        raise SystemExit(2)
    wm = res.get("watermark") or "-"
    note = " (অফলাইন)" if res.get("offline") else ""
    print(f"✅ লাইসেন্স ঠিক আছে · watermark: {wm}{note}", flush=True)
    return res


def secrets() -> dict:
    """কী-গুলো সার্ভার থেকেই আসে; মেমোরিতে ৩০ মিনিট ক্যাশ।"""
    now = time.time()
    if _last.get("at") and now - _last["at"] < 1800:
        return _last["secrets"]
    res = verify("secrets", with_secrets=True)
    if not res.get("ok"):
        raise RuntimeError(res.get("message") or "সিক্রেট আনতে পারিনি")
    _last.update({"at": now, "secrets": res.get("secrets") or {}})
    return _last["secrets"]


def secret(name: str) -> str:
    val = secrets().get(name)
    if not val:
        raise RuntimeError(f"সার্ভার '{name}' দেয়নি — লাইসেন্স বা সার্ভার ঠিকানা দেখুন")
    return val
