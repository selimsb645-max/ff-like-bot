"""প্রতিটি চালু-স্ক্রিপ্টের দরজা: লাইসেন্স + (থাকলে) লগইন।"""
from __future__ import annotations

from . import license as licence


def enforce(entry: str) -> None:
    licence.require(entry)
    try:
        import auth  # রেপোর নিজের লগইন গেট
        auth.require_auth()
    except SystemExit:
        raise
    except Exception:
        pass
