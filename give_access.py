#!/usr/bin/env python3
"""Give one person access, end to end, in a single command.

    python3 give_access.py rahim

What it does, in order:
1. refreshes the repo (git pull) so you never issue a key on a stale copy,
2. issues a fresh access key for that person (printed once),
3. commits and pushes access_keys.json, because the key only works on THEIR
   clone after it reaches GitHub,
4. prints a ready-to-send message with the clone link, the GitHub username,
   the place to paste their token and the access key.

The owner token is read from GITHUB_USAGE_TOKEN, GH_TOKEN, or ~/.ff_owner_token
(asked once, saved with 0600). It needs Contents: Read and write on this repo.
"""
from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys

import access

REPO = "https://github.com/selimsb645-max/ff-like-bot.git"
SLUG = "selimsb645-max/ff-like-bot"
TOKEN_FILE = os.path.expanduser("~/.ff_owner_token")


def _owner_token() -> str:
    for env in ("GITHUB_USAGE_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(env, "").strip()
        if val:
            print(f"[give] using owner token from ${env}")
            return val
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            val = fh.read().strip()
        if val:
            return val
    print("A GitHub token with Contents: Read and write on this repo is needed")
    print("once, so the key can be published. Create it here:")
    print("  https://github.com/settings/personal-access-tokens/new")
    print("  Only select repositories -> ff-like-bot  |  Contents: Read and write")
    val = getpass.getpass("Paste the token: ").strip()
    if not val:
        raise SystemExit("no token - aborting")
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(val)
    os.chmod(TOKEN_FILE, 0o600)
    print(f"[give] token saved to {TOKEN_FILE}")
    return val


def _redact(text: str) -> str:
    """Never print a token, even when the command contains one."""
    text = re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", text)
    text = re.sub(r"(gh[pousr]_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,})",
                  "***", text)
    return text


def _run(cmd: list[str], check: bool = False) -> int:
    print("[give]", _redact(" ".join(cmd[:6]) + (" ..." if len(cmd) > 6 else "")))
    r = subprocess.run(cmd, text=True, capture_output=True)
    if r.stdout.strip():
        print("       " + _redact(r.stdout.strip().splitlines()[-1][:160]))
    if r.returncode and check:
        print("       failed:", _redact((r.stderr or r.stdout).strip()[-200:]))
    return r.returncode


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 give_access.py <name>")
        return 2
    who = argv[1].strip()
    token = _owner_token()

    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)

    # 1) fresh copy, so the new key is added on top of the published file
    _run(["git", "pull", "--rebase", "--quiet", REPO, "main"])

    # 2) the key itself
    key, rec = access.issue_key(who)

    # 3) publish it: their clone can only accept a key that exists on GitHub
    _run(["git", "add", "access_keys.json"])
    _run(["git", "commit", "-m", f"access: key for {who}"])
    push = _run(["git", "push", "--quiet",
                 f"https://x-access-token:{token}@github.com/{SLUG}.git",
                 "HEAD:main"])
    if push:
        print("\n[give] WARNING: the push failed, so the key will NOT work on")
        print("       their clone yet. Fix the token's write permission and")
        print("       run: git push   (or run this command again)\n")

    print("=" * 62)
    print(f"Send this to {who} (change nothing except adding the token):")
    print("=" * 62)
    print(f"""
Hi, here is your access.

1) First, clone:
   git clone {REPO}
   Username : selimsb645-max
   Password : <paste the GitHub token I will send you here>

2) Then install and start:
   cd ff-like-bot
   pip install -r requirements.txt
   BOT_TOKEN='<my telegram token>' python3 bot.py

3) It will ask:
   Access key ({access.ATTEMPTS} tries left): {key}

Your key works on one phone only - the first phone that uses it.
Do not share it.
""")
    print("=" * 62)
    print("Reminder: also create their GitHub token")
    print("  https://github.com/settings/personal-access-tokens/new")
    print(f"  name: ff-{who} | ff-like-bot | Contents: Read-only | 30 days")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    # লাইসেন্স + লগইন গেট (guard/gate.py)
    from guard.gate import enforce as _enforce
    _enforce("give_access.py")

    raise SystemExit(main(sys.argv))
