#!/usr/bin/env python3
"""IMPORT GUESTS — take guest accounts that the REAL game client created.

Why this exists (research, 2026-11-05)
--------------------------------------
Every public implementation that actually credits likes — kaifcodec's toolkit is
the clearest example — gets its guest accounts by hooking the running game with
Frida and reading uid / password / token out of the app. They never register a
guest over HTTP the way guestgen.py does.

That difference is the whole story of this week: accounts we create over HTTP get
"Account banned - Reason: Cheat" or never get a profile, and LikeProfile answers
200 while the counter stays put. An account created by the genuine client is
already trusted, so its likes count.

The practical no-root route is the file the game keeps on the device:

    /sdcard/Android/data/com.dts.freefireth/shared_prefs/
        com.dts.freefireth.v2.playerprefs.xml        (Free Fire)
        com.dts.freefiremax.v2.playerprefs.xml       (Free Fire MAX)

Open it with MT Manager (it has all-files access on Android 11+), copy it here,
and this script turns it into pool rows.

    python3 import_guests.py path/to/playerprefs.xml
    python3 import_guests.py path/to/playerprefs.xml --write      # merge into bd_pool.json

Nothing is sent to Garena. The accounts keep whatever they already are; only the
credentials are extracted.
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

POOL = Path(__file__).parent / "bd_pool.json"

# Every key name Garena has used for the guest credential pair. Matching is
# case-insensitive and substring-based because the names differ per build.
UID_KEYS = ("guest_uid", "guestid", "guest_id", "uid", "account_id", "open_id")
PW_KEYS = ("guest_password", "guestpwd", "guest_pass", "password", "pwd", "token")
NICK_KEYS = ("nickname", "player_name", "ign", "name")


def parse_prefs(path: Path) -> dict:
    """Return every scalar string stored in the prefs file."""
    out = {}
    raw = path.read_bytes()

    # normal Android prefs are XML; some builds store a protobuf-ish blob, so
    # fall back to a regex sweep over printable key/value pairs
    try:
        root = ET.fromstring(raw)
        for el in root.iter():
            if el.tag in ("string", "int", "long", "boolean", "float"):
                if el.attrib.get("name"):
                    out[el.attrib["name"]] = (el.text or el.attrib.get("value") or "").strip()
    except ET.ParseError:
        pass

    if not out:
        for m in re.finditer(rb'name="([^"]+)"[^>]*>([^<]{0,120})<', raw):
            out[m.group(1).decode("utf-8", "replace")] = m.group(2).decode("utf-8", "replace").strip()
    return out


def pick(prefs: dict, keys) -> str:
    for want in keys:
        for k, v in prefs.items():
            if want == k.lower() or want in k.lower():
                if v and not v.lower().startswith(("unknown", "null", "none")):
                    return v
    return ""


def to_record(prefs: dict) -> dict | None:
    uid = pick(prefs, UID_KEYS)
    pw = pick(prefs, PW_KEYS)
    uid_digits = re.sub(r"\D", "", uid)
    if len(uid_digits) < 6:
        return None
    rec = {
        "uid": uid_digits,
        "pass": pw,
        "nick": pick(prefs, NICK_KEYS),
        "region": "BD",
        "source": "real-client",
        "profiled": True,          # the client already made this account real
    }
    if not pw:
        rec["note"] = "no password found in prefs - token-only account"
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prefs", help="path to com.dts.freefire*.v2.playerprefs.xml")
    ap.add_argument("--write", action="store_true", help="merge the result into bd_pool.json")
    ap.add_argument("--json", action="store_true", help="print the record and exit")
    a = ap.parse_args()

    path = Path(a.prefs)
    if not path.exists():
        print(f"file not found: {path}")
        return 2

    prefs = parse_prefs(path)
    print(f"keys found: {len(prefs)}")
    for k in list(prefs)[:12]:
        v = prefs[k]
        shown = v if len(v) <= 24 else v[:10] + "..." + v[-6:]
        print(f"  {k} = {shown}")

    rec = to_record(prefs)
    if not rec:
        print("\nno guest uid in this file - is this the right prefs file?")
        return 1

    print(f"\nuid    : {rec['uid']}")
    print(f"pass   : {'yes (' + str(len(rec['pass'])) + ' chars)' if rec['pass'] else 'MISSING'}")
    print(f"nick   : {rec['nick'] or '-'}")

    if a.json:
        print(json.dumps(rec))
        return 0

    if a.write:
        try:
            pool = json.loads(POOL.read_text())
        except Exception:
            pool = []
        if not isinstance(pool, list):
            pool = list(pool.values())
        if any(str(r.get("uid")) == rec["uid"] for r in pool):
            print("already in the pool - nothing written")
            return 0
        pool.append(rec)
        POOL.write_text(json.dumps(pool, indent=2))
        try:
            POOL.with_suffix(".backup.json").write_text(json.dumps(pool, indent=2))
        except Exception:
            pass
        print(f"added to {POOL.name} (pool size {len(pool)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
