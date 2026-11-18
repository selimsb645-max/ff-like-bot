#!/usr/bin/env bash
# ফোনের BD আইপিকে প্রক্সি হিসেবে বের করে আনে।
# আগের সংস্করণ শুধু PHONE_PROXY_IP সিক্রেটের আইপি ধরে নিত; ফোনের Tailscale আইপি বদলে গেলে
# ওটা মরা ঠিকানায় গিয়ে "timed out" দিত। এখন tailnet-এর সব পিয়ার ঘুরে দেখে,
# কোনটা আসলে ১০৮০ পোর্টে সাড়া দেয় এবং কোন দেশ থেকে বেরোয়, সেটা যাচাই করে।
set -uo pipefail

if [ -z "${TS_AUTHKEY:-}" ]; then
  echo "no TAILSCALE_AUTHKEY — এই ধাপ বাদ"
  exit 1
fi

curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1
sudo tailscaled > /tmp/tailscaled.log 2>&1 &
sleep 4
sudo tailscale up --authkey="$TS_AUTHKEY" --hostname="${TS_HOSTNAME:-ff-gh-probe}" --accept-routes --reset >/dev/null 2>&1

echo "=== tailnet peers ==="
sudo tailscale status || true

# প্রার্থী: সিক্রেটের আইপি + tailnet-এর সব IPv4 পিয়ার
sudo tailscale status --json > /tmp/ts.json 2>/dev/null || echo '{}' > /tmp/ts.json
python3 - <<'PY' > /tmp/cands.txt
import json, os
cands = []
secret = (os.environ.get("PHONE_PROXY_IP") or "").strip()
if secret:
    cands.append(secret)
try:
    d = json.load(open('/tmp/ts.json'))
    phone_like, others = [], []
    for p in (d.get('Peer') or {}).values():
        host = (p.get('HostName') or '').lower()
        os_name = (p.get('OS') or '').lower()
        for ip in (p.get('TailscaleIPs') or []):
            if ip.count('.') != 3:
                continue
            line = f"# peer {p.get('HostName')} os={p.get('OS')} online={p.get('Online')} ip={ip}"
            print(line, file=__import__('sys').stderr)
            if os_name == 'android' or 'realme' in host or 'c55' in host or 'phone' in host:
                phone_like.append(ip)
            else:
                others.append(ip)
    cands.extend(phone_like + others)
except Exception as e:
    print(f"# json err {e}", file=__import__('sys').stderr)
seen = []
for c in cands:
    if c not in seen:
        seen.append(c)
# tailnet-এ ৯০+ পিয়ার থাকতে পারে; ফোনের মতো পিয়ার আগে, তারপর বাকিরা, সর্বোচ্চ ৮টা
import re as _re
def rank(ip):
    return 0 if ip == secret else 1
seen.sort(key=rank)
print('\n'.join(seen[:8]))
PY
echo "=== candidates ==="; cat /tmp/cands.txt

WORKING=""
while read -r IP; do
  [ -z "$IP" ] && continue
  if python3 - "$IP" <<'PY'
import socket, sys
ip = sys.argv[1]
try:
    s = socket.create_connection((ip, 1080), 8)
    s.sendall(b"\x05\x01\x00")
    ok = s.recv(2) == b"\x05\x00"
    s.close()
    sys.exit(0 if ok else 1)
except Exception:
    sys.exit(1)
PY
  then
    echo "socks5 ALIVE at $IP:1080"
    CC=$(python3 - "$IP" <<'PY'
import sys, httpx
u = f"socks5://{sys.argv[1]}:1080"
try:
    j = httpx.get("https://ipinfo.io/json", proxy=u, timeout=12).json()
    print(j.get("country") or "?", j.get("ip"), j.get("org") or "")
except Exception as e:
    print("ERR", str(e)[:60], "")
PY
)
    echo "  exit country: $CC"
    if [ "${CC%% *}" = "BD" ] && [ -z "$WORKING" ]; then
      WORKING="$IP"
    fi
  else
    echo "socks5 dead at $IP:1080"
  fi
done < /tmp/cands.txt

if [ -z "$WORKING" ]; then
  echo "::error::কোনো ফোন প্রক্সি পাওয়া যায়নি — Termux-এ microsocks চলছে কি না, আর ফোনের Tailscale অন আছে কি না দেখুন"
  exit 1
fi
echo "PHONE_PROXY_URL=socks5://$WORKING:1080" >> "$GITHUB_ENV"
echo "using phone proxy $WORKING:1080"
