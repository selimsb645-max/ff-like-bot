#!/usr/bin/env bash
# ফোনের BD আইপিকে প্রক্সি হিসেবে সেট করে (Tailscale + microsocks)।
# সফল হলে PHONE_PROXY_URL=$GITHUB_ENV-তে লেখা হয়, তাই পরের ধাপগুলো ওটা পায়।
set -uo pipefail

if [ -z "${TS_AUTHKEY:-}" ]; then
  echo "no TAILSCALE_AUTHKEY — এই ধাপ বাদ, যা মিন্ট হবে তা BD আইপি থেকে হবে না"
  exit 0
fi

curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1
sudo tailscaled > /tmp/tailscaled.log 2>&1 &
sleep 4
sudo tailscale up --authkey="$TS_AUTHKEY" --hostname="${TS_HOSTNAME:-ff-gh-mint}" --accept-routes --reset
sudo tailscale status || true

if [ -z "${PHONE_PROXY_IP:-}" ]; then
  PHONE_PROXY_IP=$(sudo tailscale status --json | python3 -c 'import json,sys
d=json.load(sys.stdin)
c=[]
for p in (d.get("Peer") or {}).values():
    ips=[a for a in (p.get("TailscaleIPs") or []) if a.count(".")==3]
    if p.get("HostName","").lower().startswith("ff-gh-"): continue
    if ips: c.append(ips[0])
print(c[0] if c else "")')
fi
echo "phone proxy ip: ${PHONE_PROXY_IP:-<none>}"
[ -z "${PHONE_PROXY_IP:-}" ] && exit 0

URL="socks5://${PHONE_PROXY_IP}:1080"
python3 - "$URL" <<'PY'
import sys, httpx
u = sys.argv[1]
try:
    j = httpx.get("https://ipinfo.io/json", proxy=u, timeout=25).json()
    print(f"PHONE PROXY OK | exit {j.get('ip')} | {j.get('country')} | {j.get('org')}")
    if j.get("country") != "BD":
        print("WARNING: exit is not BD — মিন্ট হওয়া অ্যাকাউন্ট BD স্ট্যাম্প পাবে না")
except Exception as e:
    print("PHONE PROXY UNREACHABLE:", str(e)[:160])
    sys.exit(1)
PY
if [ $? -eq 0 ]; then
  echo "PHONE_PROXY_URL=$URL" >> "$GITHUB_ENV"
fi
