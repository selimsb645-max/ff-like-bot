#!/usr/bin/env python3
"""Import accounts produced by DARK MASTER's GUEST_GEN_FIXED.py into our owned pool.

The generator writes <PREFIX>/<PREFIX>.json (uid, password, name, account_id, region)
and <PREFIX>/jwt_tokens.json (uid, name, account_id, open_id, access_token, jwt,
region, created_at). Their JWT is the long 819-char one, which is what the game and
the like endpoint want, but they do not store key/iv, so presence (entering the game)
still has to be done by us with a fresh full-device login.

    python3 import_darkmaster.py /path/to/DARK_MASTER [--prefix DARK_MASTER]
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
OWNED = ROOT / "bd_pool_owned.json"
TOKEN_LIFE = 6 * 3600


def load_owned():
    try:
        return json.loads(OWNED.read_text())
    except Exception:
        return []


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: import_darkmaster.py <folder with accounts.json + jwt_tokens.json>")
    folder = Path(sys.argv[1])
    if folder.is_file():
        folder = folder.parent

    accounts = {}
    for name in ("accounts.json", f"{folder.name}.json", "DARK_MASTER.json"):
        f = folder / name
        if f.exists():
            for a in json.loads(f.read_text()):
                accounts[str(a.get("uid"))] = a
            break
    tokens = {}
    f = folder / "jwt_tokens.json"
    if f.exists():
        for t in json.loads(f.read_text()):
            tokens[str(t.get("uid"))] = t

    if not accounts and not tokens:
        sys.exit(f"no accounts.json / jwt_tokens.json in {folder}")

    owned = load_owned()
    by_uid = {str(r.get("uid")): r for r in owned}
    now = time.time()
    added = updated = kept_region = 0

    for uid, a in accounts.items():
        if str(a.get("region", "BD")).upper() != "BD":
            kept_region += 1
            continue
        t = tokens.get(uid, {})
        jwt = t.get("jwt") or ""
        created = 0.0
        try:
            created = time.mktime(time.strptime(t.get("created_at", ""), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
        fresh = bool(jwt) and (now - created) < TOKEN_LIFE
        rec = {
            "uid": uid, "pass": a.get("password") or a.get("pass"), "owned": True,
            "account_id": a.get("account_id") or t.get("account_id"),
            "token": jwt, "server_url": "https://clientbp.ppmainecoonghj.com",
            "region": "BD", "nick": a.get("name") or t.get("name"),
            "exp": (created + TOKEN_LIFE) if fresh else 0,
            "created": created or now, "profiled": None, "last_ok": now,
            "source": "darkmaster",
        }
        if uid in by_uid:
            old = by_uid[uid]
            old.update({k: v for k, v in rec.items() if v not in (None, "", 0)})
            updated += 1
        else:
            owned.append(rec)
            by_uid[uid] = rec
            added += 1

    OWNED.write_text(json.dumps(owned, indent=1))
    live = sum(1 for r in owned if (r.get("exp") or 0) > now + 60)
    print(f"imported {added} new, {updated} updated, skipped {kept_region} non-BD")
    print(f"owned pool: {len(owned)} records, {live} with a live token "
          f"(the rest need renew: python3 supply.py --ensure N)")


if __name__ == "__main__":
    # লাইসেন্স + লগইন গেট (guard/gate.py)
    from guard.gate import enforce as _enforce
    _enforce("import_darkmaster.py")

    main()
