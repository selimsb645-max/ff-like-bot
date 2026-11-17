#!/usr/bin/env python3
"""One-command setup for a new person on their own phone or server.

    python3 join.py                 set up this machine, then start the bot
    python3 join.py --check         only verify what is already set up
    python3 join.py --token 123:ABC  non-interactive (servers, CI)
    python3 join.py --no-start      set up but do not launch the bot

What it does, in order:

  1. makes sure this machine has its OWN bot token from @BotFather. One token
     can only be polled by one running copy, so a shared token is refused.
  2. asks that token who it is (getMe) and checks nobody else is polling it.
  3. writes it to .env next to the code, readable only by this user.
  4. runs the access gate: the NAI-... code from the owner's bot is bound to
     this machine's fingerprint. That binding is what the owner can revoke.
  5. starts bot.py.

The token never leaves this machine except to Telegram itself.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"

# tokens that belong to the owner's bots, not to a new person
OWNER_BOTS = {
    "nai_accessbot", "fflikedb_bot", "ff_new_top_up_bot",
    "darksidegatebot", "fflikebot", "ffbd_bot",
}

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


# ── little helpers, same look as the access gate ───────────────────────────
try:
    from access import C, GRAY, BOLD, CYAN, YELLOW, GREEN, RED
except Exception:                                        # pragma: no cover
    def C(text: str, code: str = "") -> str:
        return text
    GRAY = BOLD = CYAN = YELLOW = GREEN = RED = ""


def step(n: int, text: str) -> None:
    print("  " + C(str(n), BOLD) + "  " + text)


def ok(text: str) -> None:
    print("     " + C("\u2713 " + text, GREEN))


def bad(text: str) -> None:
    print("     " + C("\u2715 " + text, RED))


def note(text: str) -> None:
    print("     " + C(text, GRAY))


def ask(question: str, default: str = "y") -> bool:
    hint = " [Y/n] " if default == "y" else " [y/N] "
    try:
        answer = input(C(question + hint, YELLOW)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(1)
    if not answer:
        return default == "y"
    return answer.startswith("y")


# ── telegram calls ─────────────────────────────────────────────────────────
def api(token: str, method: str, params: dict | None = None, timeout: int = 20) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(params or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as ex:
        try:
            return json.loads(ex.read() or b"{}")
        except Exception:
            return {"ok": False, "description": f"HTTP {ex.code}"}
    except Exception as ex:
        return {"ok": False, "description": str(ex)}


def read_env() -> dict:
    out: dict[str, str] = {}
    if not ENV_FILE.exists():
        return out
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip("'").strip('"')
    return out


def write_env(values: dict) -> None:
    ENV_FILE.write_text(
        "# written by join.py - your own bot token, keep this file private\n"
        + "".join(f"{k}={v}\n" for k, v in values.items()),
        encoding="utf-8",
    )
    try:
        os.chmod(ENV_FILE, 0o600)
    except Exception:
        pass


def main(argv: list[str]) -> int:
    token_arg = ""
    do_check = "--check" in argv
    no_start = "--no-start" in argv
    if "--token" in argv:
        token_arg = argv[argv.index("--token") + 1].strip() if argv.index("--token") + 1 < len(argv) else ""

    env = read_env()
    print()
    print("  " + C("FF BD bot setup", CYAN + ";1") + C(f"  \u00b7  {HERE}", GRAY))
    print()

    # ---- 1. the token -----------------------------------------------------
    token = token_arg or env.get("BOT_TOKEN", "")
    if token and not do_check:
        if not ask("this machine already has a token saved - keep using it?", "y"):
            token = ""

    while not token:
        print("  " + C("Create your own bot:", GRAY))
        note("Telegram -> @BotFather -> /newbot -> pick a name -> copy the token")
        print()
        try:
            token = input(C("  Paste your bot token: ", YELLOW)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        token = token.removeprefix("bot").strip()
        if not TOKEN_RE.match(token):
            bad("that does not look like a bot token (123456:ABC...)")
            token = ""
        print()

    # ---- 2. who is this token? --------------------------------------------
    step(1, "asking Telegram whose token this is")
    me = api(token, "getMe")
    if not me.get("ok"):
        bad(f"Telegram refused this token: {me.get('description', 'unknown error')}")
        note("copy it again from @BotFather, /mybots -> API Token")
        return 1
    username = (me.get("result") or {}).get("username") or ""
    first = (me.get("result") or {}).get("first_name") or ""
    ok(f"@{username}  ({first})")
    if username.lower() in OWNER_BOTS:
        bad("this is the owner's bot, not a personal one")
        note("using it here would fight the owner's copy for updates (409)")
        note("create your own with @BotFather first")
        return 1

    # ---- 3. is some other copy already polling it? -------------------------
    step(2, "checking the token is not already running somewhere else")
    probe = api(token, "getUpdates", {"timeout": 0, "limit": 1, "offset": -1})
    if not probe.get("ok"):
        desc = str(probe.get("description", ""))
        if "webhook" in desc.lower():
            bad("this bot has a webhook set, so polling cannot read it")
            note("in @BotFather: /mybots -> your bot -> Bot Settings ->")
            note("Delete webhook (or pause whatever set it), then run this again")
            return 1
        if "409" in desc or "terminated by other getUpdates" in desc:
            bad("this token is already in use by another running copy")
            note("one token = one running bot. Stop the other copy, or make a")
            note("second bot with @BotFather and use that token here.")
            return 1
        note(f"could not probe for conflicts ({desc or 'no answer'}), carrying on")
    else:
        ok("token is free")

    if do_check:
        print()
        print("  " + C("check only - nothing written", GRAY))
        return 0

    env["BOT_TOKEN"] = token
    write_env(env)
    print()
    ok(f"saved to {ENV_FILE}")

    # ---- 4. the access code ------------------------------------------------
    print()
    step(3, "activating this machine with an access code")
    try:
        import access
    except Exception as ex:
        bad(f"access.py is missing or broken: {ex}")
        return 1
    try:
        access.require_access("FF BD bot")
    except SystemExit as ex:
        return int(ex.code or 1)

    # ---- 5. run it ---------------------------------------------------------
    missing = []
    for mod in ("telebot", "httpx"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        bad("python packages missing: " + ", ".join(missing))
        note("run:  pip install -r requirements.txt")
        return 1

    print()
    ok("this machine is set up")
    note(f"device: {access.device_fingerprint()[:8]}\u2026")
    print()
    if no_start:
        print("  " + C("start it with:", GRAY) + "  python3 bot.py")
        return 0
    if not ask("start the bot now?", "y"):
        print("  " + C("start it with:", GRAY) + "  python3 bot.py")
        return 0

    print()
    print("  " + C("=" * 56, GRAY))
    os.execv(sys.executable, [sys.executable, str(HERE / "bot.py")])
    return 0


if __name__ == "__main__":
    # লাইসেন্স + লগইন গেট (guard/gate.py)
    from guard.gate import enforce as _enforce
    _enforce("join.py")

    raise SystemExit(main(sys.argv[1:]))
