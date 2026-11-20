"""প্রতিটি চালু-স্ক্রিপ্টের দরজা: লাইসেন্স + (থাকলে) লগইন।"""
from __future__ import annotations

from . import license as licence


def _skip() -> bool:
    """FF_SKIP_ACCESS=1 -> the whole gate is a no-op (owner, 2026-11-20)."""
    import os
    return os.environ.get("FF_SKIP_ACCESS", "").strip().lower() in ("1", "true", "yes", "on")


def enforce(entry: str) -> None:
    if _skip():
        print("[gate] skipped (FF_SKIP_ACCESS=1) - licence/device check is off")
        return
    licence.require(entry)
    try:
        import auth  # রেপোর নিজের লগইন গেট
        auth.require_auth()
    except SystemExit:
        raise
    except Exception:
        pass
