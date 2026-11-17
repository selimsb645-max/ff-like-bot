#!/usr/bin/env python3
"""লাইসেন্স কোড বসানো/দেখা/মুছে ফেলা।

    python3 guard/cli.py login NAI-XXXX-XXXX-XXXX
    python3 guard/cli.py status
    python3 guard/cli.py logout
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guard import config  # noqa: E402
from guard.device import fingerprint  # noqa: E402
from guard.license import verify  # noqa: E402


def login(code: str) -> int:
    import os
    os.environ["FF_LICENSE"] = code.strip().upper()
    config.CODE = code.strip().upper()
    res = verify("guard/cli.py")
    if not res.get("ok"):
        print("⛔", res.get("message") or res.get("error"))
        return 2
    config.LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.LICENSE_FILE.write_text(json.dumps({"code": config.CODE}, indent=1), encoding="utf-8")
    print(f"✅ চালু · watermark: {res.get('watermark')} · ডিভাইস: {fingerprint()}")
    return 0


def status() -> int:
    from guard.license import code as current, _load
    c = current()
    print(f"কোড: {c or '(নেই)'}\nডিভাইস: {fingerprint()}\nসার্ভার: {config.GUARD_URL or '(সেট করা নেই)'}")
    last = _load(config.LAST_OK_FILE)
    if last.get("at"):
        import datetime
        print("শেষ সফল যাচাই:", datetime.datetime.fromtimestamp(last["at"]).strftime("%Y-%m-%d %H:%M"))
    if c:
        res = verify("guard/cli.py")
        print("এখন:", "ঠিক আছে" if res.get("ok") else (res.get("message") or res.get("error")))
        return 0 if res.get("ok") else 2
    return 1


def logout() -> int:
    for f in (config.LICENSE_FILE, config.LAST_OK_FILE):
        try:
            f.unlink()
        except Exception:
            pass
    print("লাইসেন্স মুছে ফেলা হলো")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "login" and len(sys.argv) > 2:
        sys.exit(login(sys.argv[2]))
    if cmd == "status":
        sys.exit(status())
    if cmd == "logout":
        sys.exit(logout())
    print(__doc__)
    sys.exit(1)
