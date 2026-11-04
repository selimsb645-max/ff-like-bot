#!/usr/bin/env bash
# One command setup for a fresh Termux install. Run after cloning the repo:
#   cd ff-like-bot && bash start_here.sh
#
# It installs dependencies, keeps Android from freezing the process, proves the phone
# really goes out as Bangladesh, saves the Telegram token and the login into .env (so
# nothing secret is typed into a chat), then starts the like bot and the account
# activator under a self-restarting wrapper.
set -uo pipefail
cd "$(dirname "$0")"
if [ ! -f bot.py ] || [ ! -d levelbot ]; then
  echo "This must run inside the ff-like-bot folder."
  echo "Expected: ~/ff-like-bot/bot.py — you are in: $(pwd)"
  exit 1
fi

say() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
ENV_FILE=".env"

say "1/6 packages"
pkg install -y python git >/dev/null 2>&1 || true

say "2/6 python libraries"
pip install -q --upgrade pip >/dev/null 2>&1 || true
if ! pip install -q -r requirements.txt; then
  echo "pip install failed — check the internet connection and run: pip install -r requirements.txt"
  exit 1
fi
# like_pb2 needs a modern protobuf; an older one gets installed by other tools
python3 - <<'PY' || pip install -q --upgrade protobuf
import sys
try:
    import google.protobuf, like_pb2  # noqa: F401
    v = tuple(int(x) for x in google.protobuf.__version__.split(".")[:2])
    sys.exit(0 if v >= (4, 25) else 1)
except Exception:
    sys.exit(1)
PY

say "3/6 keeping the CPU awake"
termux-wake-lock 2>/dev/null && echo "wake-lock held" || \
  echo "(optional: install the Termux:API app to hold a wake-lock; the bot still runs)"

say "4/6 is this phone on a Bangladesh address?"
IPJSON=$(curl -s --max-time 25 https://ipinfo.io/json)
COUNTRY=$(printf '%s' "$IPJSON" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('country',''))
except Exception: print('')")
CITY=$(printf '%s' "$IPJSON" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('city',''))
except Exception: print('')")
echo "exit country: ${COUNTRY:-unknown} (${CITY:-?})"
if [ "$COUNTRY" != "BD" ]; then
  echo "STOP: Garena stamps the account's region from this IP. Turn off any VPN and use"
  echo "      mobile data or your home broadband, then run this script again."
  exit 2
fi

say "5/6 secrets (typed here, never sent anywhere)"
token_ok() { [ -n "${1:-}" ] && printf '%s' "$1" | grep -qE '^[0-9]{6,}:[A-Za-z0-9_-]{30,}$'; }

BOT_TOKEN=""
if [ -f "$ENV_FILE" ]; then
  BOT_TOKEN=$(grep -E '^BOT_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)
fi
if ! token_ok "$BOT_TOKEN"; then
  echo "Paste the token from @BotFather (it stays on this phone):"
  read -r -p "  BOT_TOKEN: " BOT_TOKEN
fi
if ! token_ok "$BOT_TOKEN"; then
  echo "That does not look like a bot token (expected 123456789:AA...)."
  exit 3
fi

LOGIN_USER="selim"
LOGIN_PASS=""
if [ -f "$ENV_FILE" ]; then
  LOGIN_USER=$(grep -E '^BOT_USERNAME=' "$ENV_FILE" | head -1 | cut -d= -f2- || echo selim)
  LOGIN_PASS=$(grep -E '^BOT_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)
fi
[ -z "$LOGIN_USER" ] && LOGIN_USER="selim"
if [ -z "$LOGIN_PASS" ]; then
  echo "Bot login password for '$LOGIN_USER' (asked once, so the bot can start by itself):"
  read -r -s -p "  password: " LOGIN_PASS; echo
fi

umask 077
cat > "$ENV_FILE" <<EOF
BOT_TOKEN=$BOT_TOKEN
BOT_USERNAME=$LOGIN_USER
BOT_PASSWORD=$LOGIN_PASS
EOF
chmod 600 "$ENV_FILE"
echo ".env written (mode 600, ignored by git)"

say "6/6 starting"
export $(grep -v '^#' "$ENV_FILE" | xargs -d '\n' 2>/dev/null) || true

BOT_USER=$(curl -s --max-time 20 "https://api.telegram.org/bot$BOT_TOKEN/getMe" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin); print((d.get('result') or {}).get('username',''))
except Exception: print('')")
if [ -z "$BOT_USER" ]; then
  echo "STOP: Telegram rejected this token. Check it in @BotFather and run again."
  exit 4
fi
echo "bot: @$BOT_USER"

# One bot token can only be polled by one machine; two copies fight with 409 Conflict.
PENDING=$(curl -s --max-time 20 "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo" | python3 -c "
import json,sys
try: print((json.load(sys.stdin).get('result') or {}).get('url',''))
except Exception: print('')")
if [ -n "$PENDING" ]; then
  echo "note: a webhook is set on this bot ($PENDING); removing it so polling works"
  curl -s "https://api.telegram.org/bot$BOT_TOKEN/deleteWebhook" >/dev/null
fi

start_loop() {  # start_loop <name> <seconds between restarts> <command...>
  local name="$1"; shift; local wait="$1"; shift
  pkill -f "$name" 2>/dev/null
  sleep 1
  nohup bash -c "while true; do $* >> $name.log 2>&1; echo \"[\$(date '+%F %T')] exited, restarting in ${wait}s\" >> $name.log; sleep $wait; done" > /dev/null 2>&1 &
  echo "started $name (log: $name.log)"
}

BOUND=$(python3 - <<'PYCHK'
import sys
try:
    import access
    doc = access.load()
    ok = bool(access.is_remember(doc) and access.bound_for(access.device_fingerprint(), doc))
except Exception:
    ok = False
print("yes" if ok else "no")
PYCHK
)
if [ "$BOUND" = "yes" ]; then
  start_loop bot 20 python3 bot.py
else
  echo "this phone is not activated yet, so the bot loop was NOT started"
  echo "run it once in the foreground to enter the code from @NAI_AccessBot:"
  echo "  python3 bot.py        (then Ctrl+C and run start_here.sh again)"
fi
start_loop activate 600 python3 activate.py --pending 6

say "done"
python3 activate.py --report 2>/dev/null | head -5
cat <<TXT

Next:
  Telegram-এ আপনার বটে /start দিন          (bot: @$BOT_USER)
  লগ দেখতে:      tail -f bot.log
  অ্যাকাউন্ট দেখতে: tail -f activate.log
  সব বন্ধ করতে:   pkill -f bot.py; pkill -f activate.py

ফোন চার্জে রেখে Termux বন্ধ করবেন না। অ্যান্ড্রয়েড সেটিংসে Termux-কে
battery optimisation থেকে বাদ দিন, নাহলে কিছুক্ষণ পর অ্যান্ড্রয়েড প্রসেসটা মেরে দেবে।
TXT
