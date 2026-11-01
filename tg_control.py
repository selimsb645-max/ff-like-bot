#!/usr/bin/env python3
"""Control the FF farm from Telegram.

On the phone:
    FF_TG_TOKEN=123:ABC FF_TG_CHAT=8012564523 python3 tg_control.py

Commands:
    /status   running? matches, uptime, last log lines
    /start    start the 24/7 runner
    /stop     stop everything
    /restart  stop + start
    /log      last 25 log lines
    /msecs N  set match length in seconds (default 24)
"""
import os
import re
import subprocess
import sys
import time

import requests

TOKEN = os.environ.get("FF_TG_TOKEN", "").strip()
CHAT = os.environ.get("FF_TG_CHAT", "").strip()
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.environ.get("FF_LOG", os.path.join(HERE, "farm.log"))
API = f"https://api.telegram.org/bot{TOKEN}"


def send(text):
    try:
        requests.post(f"{API}/sendMessage", json={
            "chat_id": CHAT, "text": text, "disable_web_page_preview": True,
        }, timeout=20)
    except Exception as e:
        print("send failed:", e)


def sh(cmd):
    return subprocess.run(cmd, shell=True, cwd=HERE, capture_output=True, text=True)


def running():
    return sh("pgrep -f 'main.py' >/dev/null && echo yes || echo no").stdout.strip() == "yes"


def tail(n=25):
    try:
        with open(LOG, "r", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except Exception:
        return "(no log yet)"


def status():
    up = "🟢 RUNNING" if running() else "🔴 STOPPED"
    txt = tail(400)
    matches = len(re.findall(r"\[MATCH\]", txt))
    exp = re.findall(r"\[EXP\] \S+ Lvl(\d+)", txt)
    lvl = exp[-1] if exp else "?"
    last = "\n".join([l for l in tail(6).splitlines() if l.strip()][-3:])
    online = len(set(re.findall(r"\[OK\] (\d+)", txt)))
    return ("*FF farm* " + up + "\n"
            "accounts: " + str(online) + " online\n"
            "matches seen: " + str(matches) + "\n"
            "level(last): " + str(lvl) + "\n"
            "match len: " + os.environ.get("FF_MATCH_SECONDS", "24") + "s\n\n"
            + "```\n" + last[-600:] + "\n```")


def start():
    if running():
        return "already running"
    r = sh("./run24.sh")
    return r.stdout.strip() or "started"


def stop():
    r = sh("./run24.sh stop")
    return r.stdout.strip() or "stopped"


def main():
    if not TOKEN or not CHAT:
        print("set FF_TG_TOKEN and FF_TG_CHAT"); sys.exit(1)
    print("controller up; commands from chat", CHAT)
    send("🤖 FF controller online. /status /start /stop /restart /log")
    offset = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"timeout": 30, "offset": offset}, timeout=40)
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                if str(msg.get("chat", {}).get("id")) != CHAT:
                    continue
                text = (msg.get("text") or "").strip()
                cmd = text.split()[0].lower() if text else ""
                if cmd == "/status":
                    send(status())
                elif cmd == "/start":
                    send("starting…\n" + start())
                elif cmd == "/stop":
                    send(stop())
                elif cmd == "/restart":
                    stop(); time.sleep(2); send("restarted\n" + start())
                elif cmd == "/log":
                    send(f"```\n{tail(25)[-3000:]}\n```")
                elif cmd in ("/msecs", "/matchsecs"):
                    parts = text.split()
                    if len(parts) > 1 and parts[1].isdigit():
                        with open(os.path.join(HERE, ".msecs"), "w") as f:
                            f.write(parts[1])
                        send(f"match length set to {parts[1]}s (applies on next start)")
                    else:
                        send("usage: /msecs 24")
                elif cmd in ("/help", "/menu"):
                    send("/status /start /stop /restart /log /msecs N")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("poll error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
