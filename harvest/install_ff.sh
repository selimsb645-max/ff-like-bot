#!/usr/bin/env bash
# Install Free Fire into the emulator from an XAPK/APK mirror URL.
# FF_APK_URL (repo variable) must point at the current FF package
# (XAPK preferred: it bundles the OBB the game needs to start).
set -euo pipefail
PKG="${FF_PKG:-com.dts.freefireth}"
: "${FF_APK_URL:?FF_APK_URL repo variable is not set - see harvest/README.md}"
cd /tmp
echo "downloading FF package..."
curl -sL --retry 3 -o ff_pkg.bin "$FF_APK_URL"
file ff_pkg.bin || true
rm -rf ffx && mkdir ffx
if unzip -t ff_pkg.bin > /dev/null 2>&1; then
  unzip -o -q ff_pkg.bin -d ffx
else
  cp ff_pkg.bin ffx/game.apk
fi
APKS=$(find ffx -name '*.apk' | sort)
if [ -z "$APKS" ]; then echo "no APK found in the package"; exit 1; fi
echo "installing: $APKS"
# shellcheck disable=SC2086
adb install-multiple -r $APKS 2>/dev/null || adb install -r $APKS
OBB=$(find ffx -name '*.obb' | head -1 || true)
if [ -n "${OBB:-}" ]; then
  echo "pushing OBB: $OBB"
  adb shell mkdir -p "/sdcard/Android/obb/${PKG}"
  adb push "$OBB" "/sdcard/Android/obb/${PKG}/"
fi
adb shell pm list packages | grep -q "$PKG" || { echo "package not installed"; exit 1; }
adb shell dumpsys package "$PKG" | grep -m1 versionName || true
echo "Free Fire installed: $PKG"
