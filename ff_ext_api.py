#!/usr/bin/env python3
"""Optional read-only fallback: siambhau Free Fire Info API (free tier).

Used ONLY for player profile / stats reads when our own token pool cannot answer.
Needs FF_API_KEY (free key from t.me/SiamBhau). Nothing here writes to an account.
"""
import os
import httpx

BASE = os.environ.get("FF_API_BASE", "http://siambhau69.eu.cc").rstrip("/")
KEY = (os.environ.get("FF_API_KEY") or "").strip()


def available() -> bool:
    return bool(KEY)


def _get(path: str, params: dict):
    if not KEY:
        return None
    try:
        r = httpx.get(BASE + path, params=dict(params, key=KEY), timeout=18)
        d = r.json()
    except Exception:
        return None
    if isinstance(d, dict) and d.get("error"):
        return {"error": str(d["error"])[:90]}
    return d if isinstance(d, dict) else None


def profile(uid, region: str = "BD"):
    """Normalised player profile, or {'error': ...}."""
    d = _get("/freefireinfo/bhau", {"uid": str(uid), "region": region})
    if not d:
        return None
    if d.get("error"):
        return d
    b = d.get("basicInfo") or {}
    p = d.get("profileInfo") or {}
    out = {
        "uid": str(b.get("accountId") or uid),
        "name": b.get("nickname") or p.get("nickname"),
        "region": b.get("region") or region,
        "level": b.get("level"),
        "exp": b.get("exp"),
        "likes": b.get("liked"),
        "br_rank": b.get("rank"),
        "br_points": b.get("rankingPoints"),
        "cs_rank": b.get("csRank"),
        "cs_points": b.get("csRankingPoints"),
        "source": "freefireinfo",
    }
    if isinstance(out.get("likes"), str) and out["likes"].isdigit():
        out["likes"] = int(out["likes"])
    return out


def stats(uid, region: str = "BD"):
    p = profile(uid, region)
    return p


if __name__ == "__main__":
    # লাইসেন্স + লগইন গেট (guard/gate.py)
    from guard.gate import enforce as _enforce
    _enforce("ff_ext_api.py")

    import sys
    u = sys.argv[1] if len(sys.argv) > 1 else "9078648985"
    print("key set:", available())
    print(profile(u))
