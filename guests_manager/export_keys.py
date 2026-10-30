"""Export minted guest accounts as portable JSON keys.

Tool A of the two-stage pipeline:
  stage 1 (this script): registered_pool.json  ->  exports/<uid>.json + exports/accounts.json
  stage 2 (other tool) : each exported key is handed to a real game client
                         so the account logs in "like a human" once.

Usage:
    python3 export_keys.py              # export every account in the pool
    python3 export_keys.py --limit 20   # newest 20 only
    python3 export_keys.py --dat        # also write Garena .dat files
"""
import argparse
import json
import pathlib
import time

HERE = pathlib.Path(__file__).parent
POOL = HERE / "registered_pool.json"
OUT = HERE / "exports"


def load_pool():
    data = json.loads(POOL.read_text())
    rows = []
    for r in data:
        uid = str(r.get("uid", "")).strip()
        pw = str(r.get("pw", "")).strip()
        if uid.isdigit() and pw:
            rows.append({"uid": uid, "pw": pw, "nick": r.get("nick", ""),
                         "region": r.get("region", "BD"),
                         "created": r.get("created", r.get("ts"))})
    rows.sort(key=lambda r: int(r["uid"]), reverse=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="newest N accounts (0 = all)")
    ap.add_argument("--dat", action="store_true", help="also write Garena .dat files")
    args = ap.parse_args()

    rows = load_pool()
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("pool is empty - run gen_batch.py first")
        return

    OUT.mkdir(exist_ok=True)
    dat_dir = OUT / "dat"
    if args.dat:
        dat_dir.mkdir(exist_ok=True)

    for r in rows:
        key = {"guest_account_info": {
            "com.garena.msdk.guest_uid": r["uid"],
            "com.garena.msdk.guest_password": r["pw"],
        }, "_meta": {"nick": r["nick"], "region": r["region"],
                      "exported": time.strftime("%Y-%m-%d %H:%M:%S")}}
        (OUT / f"{r['uid']}.json").write_text(json.dumps(key, indent=1))
        if args.dat:
            (dat_dir / f"guest100067_{r['uid']}.dat").write_text(json.dumps(
                {"guest_account_info": key["guest_account_info"]}, separators=(",", ":")))

    combined = OUT / "accounts.json"
    combined.write_text(json.dumps([{"uid": r["uid"], "pw": r["pw"],
                                     "nick": r["nick"], "region": r["region"]}
                                    for r in rows], indent=1))
    print(f"exported {len(rows)} keys -> {OUT}")
    print(f"  combined : accounts.json")
    print(f"  per-key  : <uid>.json  ({len(rows)} files)")
    if args.dat:
        print(f"  .dat     : dat/guest100067_<uid>.dat")
    print(f"  newest   : {rows[0]['uid']}   oldest: {rows[-1]['uid']}")


if __name__ == "__main__":
    main()
