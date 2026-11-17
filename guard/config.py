"""guard-এর সেটিং। সবকিছু env দিয়ে বদলানো যায়, কোডে কোন গোপন মান নেই।"""
from __future__ import annotations

import os
from pathlib import Path

# সার্ভারের ঠিকানা: FF_GUARD_URL না থাকলে guard_url.txt দেখে
_DEFAULT = ""
_url = os.environ.get("FF_GUARD_URL", "").strip()
if not _url:
    _f = Path(__file__).resolve().parent.parent / "guard_url.txt"
    if _f.exists():
        _url = _f.read_text(encoding="utf-8").strip()
GUARD_URL = _url or _DEFAULT

CODE = os.environ.get("FF_LICENSE", "").strip()

STATE_DIR = Path(os.path.expanduser(os.environ.get("FF_GUARD_STATE", "~/.ffguard")))
LICENSE_FILE = STATE_DIR / "license.json"
LAST_OK_FILE = STATE_DIR / "last_ok.json"

OFFLINE_GRACE_HOURS = int(os.environ.get("FF_OFFLINE_GRACE_HOURS", "24"))
VERIFY_TIMEOUT = int(os.environ.get("FF_GUARD_TIMEOUT", "20"))
VERSION = "2026-11-17"
