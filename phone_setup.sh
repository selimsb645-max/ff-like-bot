#!/data/data/com.termux/files/usr/bin/bash
# PHONE SETUP — one command. The only thing you type is the 6-digit pairing code.
#
#   cd ~/ff-like-bot && git checkout main && git pull && bash phone_setup.sh
#
# Ports are discovered automatically from adb's mDNS list, because typing them by
# hand is where this kept going wrong. Then the script pulls the game's prefs file
# out of Android/data, imports the guest account and reports the pool.

set -u
cd "$(dirname "$0")" 2>/dev/null || true

say()  { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m%s\033[0m\n' "$*"; exit 1; }

say "1/5 · tools"
command -v adb >/dev/null 2>&1 || pkg install -y android-tools || die "could not install android-tools"
adb kill-server >/dev/null 2>&1
adb start-server >/dev/null 2>&1
echo "adb: $(command -v adb)"

say "2/5 · finding the wireless debugging ports"
cat <<'TXT'
On the phone, one time:
  Settings → Developer options → Wireless debugging  → turn ON
  → tap "Pair device with pairing code"  (keep that screen open)

The script finds the ports itself. It will ask you only for the 6-digit code.
TXT

PAIR_PORT=""; CONN_PORT=""
for _ in $(seq 1 25); do
  OUT=$(adb mdns services 2>/dev/null | tr -d '\r')
  PAIR_PORT=$(printf '%s\n' "$OUT" | awk '/_adb-tls-pairing/{n=split($NF,a,":"); print a[n]; exit}')
  CONN_PORT=$(printf '%s\n' "$OUT" | awk '/_adb-tls-connect/{n=split($NF,a,":"); print a[n]; exit}')
  [ -n "$PAIR_PORT" ] || [ -n "$CONN_PORT" ] && break
  sleep 1
done
[ -n "$PAIR_PORT" ] && echo "pairing port found: $PAIR_PORT" || warn "no pairing port advertised yet"
[ -n "$CONN_PORT" ] && echo "connect port found: $CONN_PORT" || warn "no connect port advertised yet"

if [ -z "$PAIR_PORT" ]; then
  read -r -p "pairing port (shown on that screen): " PAIR_PORT
fi
read -r -p "6-digit pairing code: " PAIR_CODE
[ -n "${PAIR_PORT:-}" ] || die "no pairing port - open the pairing screen again"

adb pair "127.0.0.1:${PAIR_PORT}" "${PAIR_CODE}" || warn "pairing failed - the code expires fast, reopen the screen and rerun"

say "3/5 · connecting"
for attempt in 1 2 3; do
  for _ in $(seq 1 10); do
    CONN_PORT=$(adb mdns services 2>/dev/null | tr -d '\r' \
      | awk '/_adb-tls-connect/{n=split($NF,a,":"); print a[n]; exit}')
    [ -n "$CONN_PORT" ] && break
    sleep 1
  done
  [ -n "$CONN_PORT" ] || read -r -p "connect port (main Wireless debugging screen): " CONN_PORT
  adb connect "127.0.0.1:${CONN_PORT}" >/dev/null 2>&1
  sleep 1
  if adb devices | tr -d '\r' | grep -q "127.0.0.1:${CONN_PORT}[[:space:]]*device"; then
    echo "connected: 127.0.0.1:${CONN_PORT}"; break
  fi
  warn "attempt ${attempt} failed - reopening the screen gives a new port, rerun the script"
  CONN_PORT=""
done
[ -n "$CONN_PORT" ] || die "could not attach. Reopen Wireless debugging (turn it off/on) and run the script again."

say "4/5 · pulling the game's prefs"
DEST="/sdcard/Download/ffprefs"
adb shell mkdir -p "$DEST" >/dev/null 2>&1
FOUND=0
for pkg in com.dts.freefireth com.dts.freefiremax; do
  PREFS="/sdcard/Android/data/${pkg}/shared_prefs"
  LIST=$(adb shell ls "$PREFS" 2>/dev/null | tr -d '\r')
  if [ -z "$LIST" ]; then
    echo "  ${pkg}: nothing in shared_prefs (not installed, or never opened)"
    continue
  fi
  echo "  ${pkg}:"
  printf '%s\n' "$LIST" | sed 's/^/    /'
  for f in $LIST; do
    case "$f" in
      *playerprefs*.xml)
        if adb shell "cp '${PREFS}/${f}' '${DEST}/${f}'" >/dev/null 2>&1; then
          echo "    copied ${f}"; FOUND=$((FOUND+1))
        else
          warn "    could not copy ${f}"
        fi
        ;;
    esac
  done
done
adb shell "ls -l ${DEST}" 2>/dev/null | tr -d '\r' | tail -6
[ "$FOUND" -gt 0 ] || die "no playerprefs.xml found. Open Free Fire in the game, create a Guest account, then rerun."

say "5/5 · importing"
for f in /sdcard/Download/ffprefs/*.xml; do
  [ -f "$f" ] || continue
  echo "--- $(basename "$f")"
  python3 import_guests.py "$f" --write || warn "import failed for $f"
done

say "pool now"
python3 whois.py --pool || true

cat <<'TXT'

Next, when the pool shows an account that is really yours:
  python3 like_canary.py --uid 9078648985 --liker <that uid> --direct
TXT
