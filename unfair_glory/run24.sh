#!/usr/bin/env bash
# Keep main.py alive 24/7 on the phone.
#   ./run24.sh           start in background
#   ./run24.sh stop      stop everything
#   ./run24.sh status    running?
#   ./run24.sh log [N]   tail the log
cd "$(dirname "$0")" || exit 1
PIDFILE=".runner.pid"
LOG="${FF_LOG:-farm.log}"

case "$1" in
  stop)
    [ -f "$PIDFILE" ] && { kill "$(cat "$PIDFILE")" 2>/dev/null; rm -f "$PIDFILE"; }
    pkill -f "main.py" 2>/dev/null
    pkill -f "tg_control.py" 2>/dev/null
    echo "stopped"; exit 0 ;;
  status)
    if pgrep -f "main.py" >/dev/null 2>&1; then
      echo "RUNNING (pid $(pgrep -f 'main.py' | head -1))"
    else
      echo "NOT running"
    fi
    exit 0 ;;
  log)
    tail -n "${2:-40}" "$LOG" 2>/dev/null || echo "(no log)"
    exit 0 ;;
esac

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock

# match length: .msecs (set from Telegram) wins, else FF_MATCH_SECONDS, else 24
[ -f .msecs ] && export FF_MATCH_SECONDS="$(cat .msecs)"
export FF_LOG="$LOG"

nohup bash -c '
  cd "'"$(pwd)"'" || exit 1
  while true; do
    echo "[runner $(date +%H:%M:%S)] start main.py (match ${FF_MATCH_SECONDS:-24}s)" >> "$FF_LOG"
    python3 -u main.py >> "$FF_LOG" 2>&1
    echo "[runner $(date +%H:%M:%S)] main.py exited ($?) - restarting in 15s" >> "$FF_LOG"
    sleep 15
  done
' >/dev/null 2>&1 &
echo $! > "$PIDFILE"
sleep 1
echo "started 24/7 runner (pid $(cat "$PIDFILE")) | log: $LOG"
