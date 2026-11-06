#!/data/data/com.termux/files/usr/bin/bash
# PHONE SETUP — one command, everything else is automatic.
#
#   cd ~/ff-like-bot && git pull && bash phone_setup.sh
#
# The only thing a machine cannot do for you is read a code off your own screen.
# This script asks for it once, then does the rest by itself: pairs adb, pulls the
# game's prefs file out of Android/data, imports the guest account into the pool
# and shows you what came back.

set -u
cd "$(dirname "$0")" 2>/dev/null || true

say()  { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m%s\033[0m\n' "$*"; exit 1; }

say "1/5 · tools"
if ! command -v adb >/dev/null 2>&1; then
  pkg install -y android-tools || die "could not install android-tools"
fi
adb kill-server >/dev/null 2>&1
adb start-server >/dev/null 2>&1
echo "adb ready: $(command -v adb)"

say "2/5 · pairing"
cat <<'TXT'
On the phone do this once:
  Settings → Developer options → Wireless debugging  (turn ON)
  → tap "Pair device with pairing code"

That screen shows TWO things: a 6-digit code and a port number (e.g. 37123).
Type the port and the code below. Nothing else is needed.
TXT

read -r -p "pairing port : " PAIR_PORT
read -r -p "6-digit code : " PAIR_CODE
[ -n "${PAIR_PORT:-}" ] && [ -n "${PAIR_CODE:-}" ] || die "no code given, stopping"

adb pair "127.0.0.1:${PAIR_PORT}" "${PAIR_CODE}" || warn "pair said no - if the port was stale, open the screen again and retry"

say "3/5 · connecting"
CONNECTED=""
for attempt in 1 2 3; do
  echo "connect port (the one on the Wireless debugging screen, not the pairing one):"
  read -r -p "connect port : " CONN_PORT
  if adb connect "127.0.0.1:${CONN_PORT}" 2>&1 | grep -qi "connected"; then
    sleep 1
    if adb devices | grep -q "127.0.0.1:${CONN_PORT}[[:space:]]*device"; then
      CONNECTED="${CONN_PORT}"; break
    fi
  fi
  warn "attempt ${attempt} failed - the port changes every time the screen is reopened"
done
[ -n "$CONNECTED" ] || die "could not connect. Reopen Wireless debugging and run this script again."
echo "connected on port ${CONNECTED}"

say "4/5 · pulling the game's prefs"
DEST="/sdcard/Download/ffprefs"
adb shell mkdir -p "$DEST" >/dev/null 2>&1
FOUND=0
for pkg in com.dts.freefireth com.dts.freefiremax; do
  PREFS="/sdcard/Android/data/${pkg}/shared_prefs"
  LIST=$(adb shell ls "$PREFS" 2>/dev/null | tr -d '\r')
  if [ -z "$LIST" ]; then
    echo "  ${pkg}: no shared_prefs (not installed, or the game was never opened)"
    continue
  fi
  echo "  ${pkg}: ${LIST}"
  for f in $LIST; do
    case "$f" in
      *playerprefs*.xml)
        adb shell "cp '${PREFS}/${f}' '${DEST}/${f}'" >/dev/null 2>&1 \
          && echo "    copied ${f}" && FOUND=$((FOUND+1))
        ;;
    esac
  done
done
adb shell "ls -l ${DEST}" 2>/dev/null | tr -d '\r' | tail -5
[ "$FOUND" -gt 0 ] || die "No playerprefs.xml found. Open Free Fire once, create a Guest account inside the game, then run this again."

say "5/5 · importing and checking"
for f in $(ls /sdcard/Download/ffprefs/*.xml 2>/dev/null); do
  echo "--- $f"
  python3 import_guests.py "$f" --write || warn "import failed for $f"
done

say "pool now"
python3 whois.py --pool || true

cat <<'TXT'

Done. Next, once the pool shows an account that is yours:
  python3 like_canary.py --uid 9078648985 --liker <that uid> --direct
TXT
