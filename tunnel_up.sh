#!/usr/bin/env bash
# Bring up the BD tunnel (tailscaled -> phone microsocks) and prove it works.
# Written because a stale chain_proxy silently sent traffic from a datacenter IP and
# made healthy likes look like failures. Run this before any live FF work.
#
#   ./tunnel_up.sh            # start + verify
#   ./tunnel_up.sh --check     # verify only
set -uo pipefail

TS=/usr/local/bin/tailscale
SOCK=/tmp/ts/tailscaled.sock
STATE=/tmp/ts/tailscaled.state
PORT="${CHAIN_PORT:-8899}"
PHONE_PORT="${PHONE_PORT:-1080}"

say() { printf '%s\n' "$*"; }

if [ "${1:-}" != "--check" ]; then
  if ! "$TS" --socket="$SOCK" status >/dev/null 2>&1; then
    say "starting tailscaled..."
    mkdir -p /tmp/ts
    setsid /usr/local/bin/tailscaled --tun=userspace-networking \
      --socks5-server=127.0.0.1:1055 --state="$STATE" --socket="$SOCK" \
      --port=41641 > /tmp/ts/tailscaled.log 2>&1 &
    sleep 9
  fi
fi

PHONE_IP=$("$TS" --socket="$SOCK" status --json 2>/dev/null | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit()
for p in (d.get('Peer') or {}).values():
    if 'android' in (p.get('OS') or '').lower() and p.get('Online'):
        ips=[i for i in (p.get('TailscaleIPs') or []) if ':' not in i]
        if ips: print(ips[0]); break
")
if [ -z "$PHONE_IP" ]; then
  say "PHONE OFFLINE — nothing can reach the game servers as BD."
  say "On the phone: open Tailscale (Connected), then in Termux run"
  say "  termux-wake-lock; pkill -f microsocks; microsocks -i 0.0.0.0 -p $PHONE_PORT &"
  exit 2
fi
say "phone: $PHONE_IP"

if [ "${1:-}" != "--check" ]; then
  pkill -f "chain_proxy.py $PORT" 2>/dev/null
  sleep 1
  setsid nohup python3 "$(dirname "$0")/../chain_proxy.py" "$PORT" 127.0.0.1:1055 \
    "$PHONE_IP:$PHONE_PORT" > /tmp/chain.log 2>&1 &
  sleep 3
fi

for i in 1 2 3 4 5; do
  JSON=$(timeout 15 curl -s --proxy "http://127.0.0.1:$PORT" https://ipinfo.io/json 2>/dev/null)
  if [ -n "$JSON" ]; then
    echo "$JSON" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f\"exit: {d.get('ip')} | {d.get('city')} | {d.get('country')} | {(d.get('org') or '')[:40]}\")
sys.exit(0 if d.get('country')=='BD' else 3)"
    rc=$?
    if [ $rc -ne 0 ]; then
      say "exit is NOT BD — do not run FF work (mobile data may be on a foreign route)"
      exit 3
    fi
    say "tunnel ready: use HTTPS_PROXY=http://127.0.0.1:$PORT and BD_PROXY_URL=http://127.0.0.1:$PORT"
    exit 0
  fi
  sleep 3
done
say "chain proxy could not reach the phone: microsocks is probably not listening on $PHONE_PORT"
exit 4
