#!/usr/bin/env bash
# One-shot setup: run the like bot ON the phone, using the phone's own BD IP.
# No Tailscale, no microsocks, no chain proxy — the traffic already leaves from a
# residential Bangladesh address, which is the only thing Garena really checks.
#
# In Termux:
#   pkg install -y git
#   git clone https://<PAT>@github.com/selimsb645-max/ff-like-bot.git
#   cd ff-like-bot && bash termux_setup.sh
set -uo pipefail
cd "$(dirname "$0")"

say() { printf '\n== %s ==\n' "$*"; }

say "packages"
pkg install -y python git termux-api >/dev/null 2>&1 || pkg install -y python git >/dev/null 2>&1

say "python deps"
pip install -q --upgrade pip
pip install -q -r requirements.txt

say "keep the CPU awake so Android does not freeze the bot"
termux-wake-lock 2>/dev/null || echo "(termux-api missing: install Termux:API app for wake-lock)"

# A guest account's region is stamped at login from the calling IP, so the phone must
# be the thing that talks to Garena. Verify before anything else.
say "checking that this phone really goes out as BD"
IPJSON=$(curl -s --max-time 20 https://ipinfo.io/json)
echo "$IPJSON" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f\"exit {d.get('ip')} | {d.get('city')} | {d.get('country')} | {(d.get('org') or '')[:40]}\")
sys.exit(0 if d.get('country')=='BD' else 1)
" || { echo "STOP: this phone is not on a BD address (turn off any VPN, use mobile data)"; exit 2; }

say "starting the bot (Telegram token comes from bot_token.txt)"
if [ ! -f bot_token.txt ]; then
  echo "put your @BotFather token in bot_token.txt first, one line, then run this again"
  exit 3
fi
export BOT_TOKEN="$(cat bot_token.txt)"

run_bg() {   # run_bg <name> <command...>
  local name="$1"; shift
  if pgrep -f "$name" >/dev/null; then echo "$name already running"; return; fi
  nohup "$@" > "$name.log" 2>&1 &
  echo "$name started (pid $!)"
}

# Accounts must be created AND activated from this device, because the login that
# stamps the region can only be spent once.
run_bg activate python activate.py --pending 6
sleep 5
run_bg bot python bot.py

say "status"
python activate.py --report
echo
echo "logs:   tail -f bot.log   |   tail -f activate.log"
echo "stop:   pkill -f bot.py"
