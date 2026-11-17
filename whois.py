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
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "guests_manager")

import ff_bd_api as api  # noqa: E402

FIELDS = ("uid", "name", "region", "level", "likes", "exp", "credit_score")



TOKEN_TTL = 40 * 60          # a BD guest JWT is good for about an hour


def fresh_token(row, force=False):
    """Return (jwt, refreshed).

    Reading a profile with an expired token returns empty fields, which looks
    exactly like a banned account. Refreshing first is the difference between a
    real answer and a wrong one.
    """
    tok = row.get("token") or ""
    ts = row.get("ts") or 0
    if tok and not force and (time.time() - ts) < TOKEN_TTL:
        return tok, False
    pw = row.get("pass") or row.get("pw")
    if pw:
        try:
            info = api.mint_jwt_self(str(row["uid"]), pw)
        except Exception:
            info = None
        if info and info.get("jwt"):
            row["token"] = info["jwt"]
            row["ts"] = int(time.time())
            return info["jwt"], True
    return tok, False


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
    ap.add_argument("--uid", default=None, help="public UID (8-10 digits)")
    ap.add_argument("--pool", action="store_true", help="check every account in bd_pool.json")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.pool:
        try:
            raw = json.loads(Path("bd_pool.json").read_text())
        except Exception as exc:
            print(f"cannot read bd_pool.json: {exc}")
            return 2
        rows = raw if isinstance(raw, list) else list(raw.values())
        rows.sort(key=lambda r: r.get("ts") or 0, reverse=True)
        print(f"{'uid':12} {'region':7} {'lvl':>4} {'likes':>6}  {'name':14} ours?")
        active = 0
        for r in rows:
            uid = str(r.get("uid"))
            tok, rf = fresh_token(r)
            try:
                prof = api.get_profile(int(uid), tok) or {}
            except Exception as exc:
                print(f"{uid:12} {'-':7} {'-':>4} {'-':>6}  unreadable ({type(exc).__name__})")
                continue
            reg, lvl, lk = prof.get("region"), prof.get("level"), prof.get("likes")
            ok = lvl is not None or prof.get("name")
            active += 1 if ok else 0
            nm = str(prof.get("name") or "")
            stored = ""
            if not ok and (r.get("verified") or r.get("live_name")):
                # the read needs a valid token; if ours is spent, trust what the
                # mint verified at the moment it had one
                stored = f"stored: {r.get('live_name') or '?'} lvl {r.get('live_level') or '?'}"
                ok = True
                reg = r.get("live_region") or reg
                lvl = r.get("live_level") or lvl
            # our accounts are minted with the "SB" + U+3164 prefix; anything else
            # in this row is not ours, however plausible the profile looks
            ours = "yes" if nm.startswith("SB\u3164") else ("??" if not nm else "NO")
            print(f"{uid:12} {str(reg or '-'):7} {str(lvl if lvl is not None else '-'):>4} "
                  f"{str(lk if lk is not None else '-'):>6}  {(nm or stored or '-')[:14]:14} {ours}")
        print(f"\n{active}/{len(rows)} accounts have an active profile")
        return 0 if active else 1

    if not args.uid:
        print("give --uid <number> or --pool")
        return 2

    try:
        cand = next((r for r in json.loads(Path("bd_pool.json").read_text())
                     if str(r.get("uid")) == str(args.uid)), {})
    except Exception:
        cand = {}
    tok, rf = fresh_token(cand) if cand else (None, False)
    print(f"token : {'refreshed' if rf else 'pool copy'}")
    prof = api.get_profile(int(args.uid), tok) or {}
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
    # লাইসেন্স + লগইন গেট (guard/gate.py)
    from guard.gate import enforce as _enforce
    _enforce("whois.py")

    raise SystemExit(main())
