#!/usr/bin/env python3
"""WHOIS — what does Garena actually hold for this account?

The one question worth answering before touching the like path again: does the
UID have a real in-game profile (name + level), or did the account never become
active? A like can only be credited from an account the server considers real.

    python3 whois.py --uid 8410381093
    python3 whois.py --uid 8410381093 --pool      # also show the local pool row
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "guests_manager")

import ff_bd_api as api  # noqa: E402

FIELDS = ("uid", "name", "region", "level", "likes", "exp", "credit_score")


def pool_row(uid: str):
    try:
        raw = json.loads(Path("bd_pool.json").read_text())
    except Exception:
        return None
    rows = raw if isinstance(raw, list) else list(raw.values())
    for r in rows:
        if isinstance(r, dict) and str(r.get("uid")) == str(uid):
            return {k: r.get(k) for k in ("uid", "region", "server_url", "profiled", "ts_human", "nick")}
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="read one account's server-side profile")
    ap.add_argument("--uid", required=True, help="public UID (8-10 digits)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    prof = api.get_profile(int(args.uid)) or {}
    row = {k: prof.get(k) for k in FIELDS}
    active = any(row.get(k) is not None for k in ("level", "name"))

    if args.json:
        print(json.dumps({"profile": row, "active": active}, ensure_ascii=False))
        return 0 if active else 1

    print(f"uid    : {args.uid}")
    for k in FIELDS[1:]:
        print(f"{k:7}: {row.get(k)}")
    print()
    if active:
        print("ACTIVE PROFILE — the account exists in game and can be used as a liker")
    else:
        print("NO PROFILE — every field came back empty:")
        print("  · the account never finished activation, or")
        print("  · the JWT is stale, or")
        print("  · the UID is wrong")
    print("local pool:", json.dumps(pool_row(args.uid), ensure_ascii=False))
    return 0 if active else 1


if __name__ == "__main__":
    raise SystemExit(main())
