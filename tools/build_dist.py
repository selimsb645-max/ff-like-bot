#!/usr/bin/env python3
"""প্রতি ইউজারের জন্য আলাদা প্যাকেজ বানায়: শুধু .pyc, নিজের ওয়াটারমার্ক, কোন সোর্স নেই।

    python3 tools/build_dist.py --user rahim --license NAI-1A2B-3C4D-5E6F --out dist

ফল: dist/ff-like-bot-rahim.zip
- সব .py কম্পাইল হয়ে .pyc, সোর্স ফাইল বাদ
- watermark.json-এ (user, license, build) — লিক হলে কার কপি ধরা পড়বে
- .git, bd.ovpn, *_tokens*.json, auth.json বাদ
"""
from __future__ import annotations

import argparse
import compileall
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", "dist", "models", "node_modules", "unfair_glory", "guests_manager/bdvpn"}
SKIP_NAMES = {"bd.ovpn", "auth.json", "dead_tokens.json", "external_tokens_cache.json", "jwt_tokens.json",
              "jwt_tokens.json.bak", "token_cache.json", "farm_log.txt", "llama-server.log"}
SKIP_SUFFIX = {".ovpn", ".log", ".zip", ".gguf", ".pyc"}


def wanted(p: Path) -> bool:
    rel = p.relative_to(ROOT).as_posix()
    if any(rel.startswith(d) for d in SKIP_DIRS):
        return False
    if p.name in SKIP_NAMES or p.suffix in SKIP_SUFFIX:
        return False
    return True


def copy_tree(dest: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "dist", "models", "node_modules")]
        for name in filenames:
            src = Path(dirpath) / name
            if not wanted(src):
                continue
            rel = src.relative_to(ROOT)
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)


def compile_tree(root: Path) -> None:
    compileall.compile_dir(str(root), quiet=1, legacy=False, optimize=2)


def strip_sources(root: Path) -> int:
    """প্রতিটি .py → পাশে .pyc, তারপর সোর্স মুছে ফেলা।"""
    import importlib.util

    removed = 0
    for py in list(root.rglob("*.py")):
        target = py.with_suffix(".pyc")
        built = None
        for opt in ("2", "1", ""):
            try:
                candidate = Path(importlib.util.cache_from_source(str(py), optimization=opt or None))
            except Exception:
                continue
            if candidate.exists():
                built = candidate
                break
        if built is None:
            print("  ! কম্পাইল হয়নি:", py.relative_to(root))
            continue
        shutil.move(str(built), str(target))
        py.unlink()
        removed += 1
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--license", default="")
    ap.add_argument("--out", default="dist")
    a = ap.parse_args()

    user = "".join(c for c in a.user if c.isalnum() or c in "-_")[:40]
    out_dir = ROOT / a.out
    work = out_dir / f"build-{user}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    copy_tree(work)
    (work / "guard_url.txt").write_text(os.environ.get("FF_GUARD_URL", ""), encoding="utf-8")
    (work / "watermark.json").write_text(json.dumps({
        "user": user,
        "license": a.license,
        "build": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mark": f"{user}-{time.strftime('%m%d%H%M')}",
    }, indent=1), encoding="utf-8")

    print("কম্পাইল হচ্ছে…")
    compile_tree(work)
    removed = strip_sources(work)
    for junk in ("__pycache__",):
        for d in work.rglob(junk):
            shutil.rmtree(d, ignore_errors=True)

    zip_path = out_dir / f"ff-like-bot-{user}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in work.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(work))
    shutil.rmtree(work, ignore_errors=True)
    print(f"হলো: {zip_path} ({zip_path.stat().st_size // 1024} KB), {removed}টি .py → .pyc, watermark: {user}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
