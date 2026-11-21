#!/data/data/com.termux/files/usr/bin/bash
# harvest_phone.sh — app-born guest loop on the phone itself.
#
# Per cycle: clear FF data -> launch the real app -> tap the guest-login
# entry -> the APP registers a fresh guest -> read the stored guest
# credentials -> append {uid, password} to /sdcard/ff_guests.json.
#
# PREREQUISITE: probe.sh must first confirm WHERE the credentials are
# readable on this build. Set CRED_FILE below to the file the probe
# found (external path), or CRED_MODE=prefs if internal prefs turned
# out readable (rooted shell only).
#
# Tap points are fractions of the screen (0..1) so they scale to the
# device; calibrate once from a screenshot of the FF login screen.
PKG="${FF_PKG:-com.dts.freefireth}"
COUNT="${1:-1}"
OUT=/sdcard/ff_guests.json
RISH="$HOME/rish"
CRED_MODE="${CRED_MODE:-external}"          # external | prefs
CRED_FILE="${CRED_FILE:-}"                  # filled in after the probe
TAP_GUEST_X=0.500; TAP_GUEST_Y=0.845        # guest icon on the login row
TAP_CONFIRM_X=0.500; TAP_CONFIRM_Y=0.580    # confirm dialog button
WAIT_BOOT=45; WAIT_REG=30                   # seconds

shx() { if [ -x "$RISH" ]; then "$RISH" -c "$*"; else sh -c "$*"; fi }
SIZE=$(shx "wm size" | grep -o '[0-9]*x[0-9]*' | tail -1)
W=${SIZE%x*}; H=${SIZE#*x}
tap() { # tap by fraction
  local x y
  x=$(awk "BEGIN{print int($1 * $W)}"); y=$(awk "BEGIN{print int($2 * $H)}")
  shx "input tap $x $y"
}

extract() { # print "uid password" from the credential source, or nothing
  local src=""
  if [ "$CRED_MODE" = "prefs" ]; then
    src=$(shx "cat /data/data/$PKG/shared_prefs/*.xml 2>/dev/null")
  elif [ -n "$CRED_FILE" ]; then
    src=$(shx "cat '$CRED_FILE' 2>/dev/null")
  fi
  [ -z "$src" ] && return 1
  local uid pw
  uid=$(printf '%s' "$src" | grep -o 'guest_uid[^0-9]*[0-9]\{6,\}' | grep -o '[0-9]\{6,\}' | head -1)
  pw=$(printf '%s' "$src" | grep -o 'guest_password[^0-9a-fA-F]*[0-9a-fA-F]\{16,\}' | grep -o '[0-9a-fA-F]\{16,\}' | head -1)
  [ -n "$uid" ] && [ -n "$pw" ] && echo "$uid $pw"
}

touch "$OUT" 2>/dev/null; grep -q '^\[' "$OUT" 2>/dev/null || echo '[]' > "$OUT"
for i in $(seq 1 "$COUNT"); do
  echo "[harvest] cycle $i/$COUNT"
  shx "pm clear $PKG" > /dev/null
  shx "monkey -p $PKG -c android.intent.category.LAUNCHER 1" > /dev/null 2>&1
  sleep "$WAIT_BOOT"
  tap $TAP_GUEST_X $TAP_GUEST_Y
  sleep 3
  tap $TAP_CONFIRM_X $TAP_CONFIRM_Y
  sleep "$WAIT_REG"
  got=$(extract) || got=""
  if [ -n "$got" ]; then
    set -- $got
    python3 - "$OUT" "$1" "$2" << 'PY'
import json, sys
path, uid, pw = sys.argv[1], sys.argv[2], sys.argv[3]
try: data = json.load(open(path))
except Exception: data = []
if not any(g.get("uid") == uid for g in data):
    data.append({"uid": uid, "password": pw})
json.dump(data, open(path, "w"), indent=1)
print(f"[harvest] saved uid={uid} (total {len(data)})")
PY
  else
    echo "[harvest] cycle $i: no credentials extracted — check CRED_FILE/taps, send a screenshot"
  fi
  shx "am force-stop $PKG"
  sleep 5
done
echo "[harvest] done -> $OUT"
