#!/usr/bin/env python3
"""Structured stage log for the mint / activation pipeline.

The pipeline used to fail silently: prints were the only record, so nobody could
tell whether an account died at register, grant, MajorRegister, login, region
lock, GetLoginData or presence. Every stage now appends one JSON line here:

    {"ts": ..., "src": "guestgen", "stage": "region_lock", "uid": "...",
     "ok": false, "reason": "no server_url"}

    python3 -c "import json; [print(json.loads(l)) for l in open('stage_log.jsonl')]"
    grep '"ok": false' stage_log.jsonl | tail -20

Logging must never break the pipeline it observes, so every failure is swallowed.
"""
import json
import time
from pathlib import Path

LOG_FILE = Path(__file__).parent / "stage_log.jsonl"


def stage(src: str, stage: str, uid=None, ok=None, reason: str = "") -> None:
    try:
        rec = {"ts": int(time.time()), "src": src, "stage": stage,
               "uid": str(uid) if uid is not None else None,
               "ok": ok, "reason": str(reason)[:200]}
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
