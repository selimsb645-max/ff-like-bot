#!/data/data/com.termux/files/usr/bin/bash
# KEEP TUNNEL — one command that never exits, on the phone.
#
#   bash keep_tunnel.sh
#
# Android kills background Termux processes (Doze / battery optimisation), which is
# why the proxy kept dying mid-test. This loop restarts microsocks and sshd whenever
# they disappear, so the tunnel stays up while the phone is charging or awake.
#
# Leave it running in one Termux session. Ctrl-C stops it.
set -u

termux-wake-lock 2>/dev/null || true

echo "keeping microsocks:1080 and sshd:8022 alive (Ctrl-C to stop)"
while true; do
  if ! pgrep -f "microsocks -i 0.0.0.0 -p 1080" >/dev/null 2>&1; then
    nohup microsocks -i 0.0.0.0 -p 1080 >/dev/null 2>&1 &
    echo "$(date +%H:%M:%S) microsocks restarted (pid $!)"
  fi
  if ! pgrep -f "sshd" >/dev/null 2>&1; then
    nohup sshd >/dev/null 2>&1 &
    echo "$(date +%H:%M:%S) sshd restarted"
  fi
  sleep 5
done
