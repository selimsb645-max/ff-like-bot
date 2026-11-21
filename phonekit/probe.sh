#!/data/data/com.termux/files/usr/bin/bash
# probe.sh — find out WHERE the Free Fire guest credentials live on this
# phone and whether a shell (Shizuku/rish) can read them. Run AFTER you
# have created ONE guest account by hand in the real FF app.
#
# Output goes to the screen and to /sdcard/ff_probe_result.txt — send
# that file (or screenshots of it) back so the harvest loop can be
# finalised for this exact device/build.
PKG="${FF_PKG:-com.dts.freefireth}"
OUT=/sdcard/ff_probe_result.txt
RISH="$HOME/rish"

shx() { # run a command with shell identity via Shizuku's rish
  if [ -x "$RISH" ]; then "$RISH" -c "$*"; else sh -c "$*"; fi
}

{
echo "===== FF guest credential probe ====="
echo "package: $PKG"
echo "-- screen size (for tap calibration):"
shx "wm size" 2>/dev/null || echo "  (rish not available?)"
echo
echo "-- [1] external data dir listing (readable via shell):"
shx "ls -laR /sdcard/Android/data/$PKG 2>/dev/null | head -80" 
echo
echo "-- [2] files under external data mentioning 'guest' (names):"
shx "find /sdcard/Android/data/$PKG -iname '*guest*' 2>/dev/null"
echo
echo "-- [3] grep for credential keys in external files:"
shx "grep -r -i -l 'guest_uid\|guest_password\|guest_token' /sdcard/Android/data/$PKG 2>/dev/null | head"
shx "grep -r -i 'guest_uid\|guest_password' /sdcard/Android/data/$PKG 2>/dev/null | head -5"
echo
echo "-- [4] internal SharedPreferences (needs root; denial is expected without it):"
shx "ls /data/data/$PKG/shared_prefs/ 2>&1 | head -20"
shx "grep -i 'guest' /data/data/$PKG/shared_prefs/*.xml 2>/dev/null | head -5"
echo
echo "-- [5] most recently modified files in external data (top 15):"
shx "find /sdcard/Android/data/$PKG -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -15 | cut -d' ' -f2-"
echo "===== probe done ====="
} | tee "$OUT"
echo
echo "Result saved to $OUT — send this file back."
