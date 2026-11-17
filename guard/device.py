"""মেশিনের স্থির পরিচয়: ফাইল কপি করলেও লাইসেন্স অন্য মেশিনে খুলবে না।"""
from __future__ import annotations

import hashlib
import os
import platform
import socket
import subprocess


def _read(path: str) -> str:
    try:
        return open(path, encoding="utf-8").read().strip()
    except Exception:
        return ""


def _android_id() -> str:
    for path in ("/data/system/packages.xml",):
        t = _read(path)
        if t:
            return hashlib.sha256(t.encode()).hexdigest()[:16]
    try:
        out = subprocess.run(["getprop", "ro.serialno"], capture_output=True, text=True, timeout=5)
        if out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def fingerprint() -> str:
    parts = [
        _read("/etc/machine-id") or _read("/var/lib/dbus/machine-id"),
        _read("/proc/sys/kernel/random/boot_id"),
        _android_id(),
        socket.gethostname(),
        platform.machine(),
        os.environ.get("USER", "") or os.environ.get("USERNAME", ""),
    ]
    raw = "|".join(p for p in parts if p)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
