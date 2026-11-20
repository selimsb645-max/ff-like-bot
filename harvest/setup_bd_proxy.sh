#!/usr/bin/env bash
# Start the HTTP->SOCKS chain to the owner's phone and verify the exit is BD.
# Requires: tailscale already up (workflow step), PHONE_PROXY_IP set (secret).
set -euo pipefail
: "${PHONE_PROXY_IP:?PHONE_PROXY_IP secret is not set}"
cd "$(dirname "$0")"
nohup python3 chain_proxy.py "$PHONE_PROXY_IP" 1080 8899 > chain_proxy.log 2>&1 &
sleep 2
COUNTRY=$(curl -s --max-time 30 --proxy http://127.0.0.1:8899 https://ipinfo.io/country || true)
echo "proxy exit country: ${COUNTRY:-<unreachable>}"
if [ "$COUNTRY" != "BD" ]; then
  echo "exit is not BD - refusing to harvest (accounts would lock to the wrong region)"
  exit 1
fi
# The emulator sees the runner host as 10.0.2.2
echo "HARVEST_PROXY=10.0.2.2:8899" >> "$GITHUB_ENV"
echo "BD proxy ready"
