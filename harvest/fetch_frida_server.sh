#!/usr/bin/env bash
# Download the frida-server build matching the installed frida version
# and start it inside the emulator (google_apis images allow adb root).
set -euo pipefail
VER=$(python3 -c "import frida; print(frida.__version__)")
echo "frida version: $VER"
cd /tmp
curl -sL -o frida-server.xz "https://github.com/frida/frida/releases/download/${VER}/frida-server-${VER}-android-arm64.xz"
xz -df frida-server.xz
adb root || true
sleep 2
adb push frida-server /data/local/tmp/frida-server
adb shell chmod 755 /data/local/tmp/frida-server
adb shell '/data/local/tmp/frida-server &' || true
sleep 3
adb shell "ps -A | grep frida" || { echo "frida-server did not start"; exit 1; }
echo "frida-server running"
