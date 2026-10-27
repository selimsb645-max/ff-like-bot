#!/bin/bash
# runner: keep bot alive + optional glory level engine until job timeout
mkdir -p pool
if [ -n "$BD_CREDS_B64" ]; then
  echo "$BD_CREDS_B64" | base64 -d > bd_creds.txt
fi
END=$(( $(date +%s) + 20500 ))  # ~5h42m, under the 6h Actions limit

# --- bot in background with its own restart loop ---
(
  while [ $(date +%s) -lt $END ]; do
    timeout 5400 python3 -u bot.py
    echo "bot exited, restarting in 10s..."
    sleep 10
  done
) &
BOT_PID=$!

# --- BD tunnel setup (SHARED: used by register pipeline AND glory engine) ---
REN=False; GEN=False
[ -f guests_manager/register_config.json ] && REN=$(python3 -c "import json;print(json.load(open('guests_manager/register_config.json')).get('enabled',False))" 2>/dev/null)
[ -f level_config.json ] && GEN=$(python3 -c "import json;print(json.load(open('level_config.json')).get('enabled',False))" 2>/dev/null)
BD_TUNNEL=0
if [ "$REN" = "True" ] || [ "$GEN" = "True" ]; then
  pip install -q pysocks 2>/dev/null || true
  if [ -f guests_manager/bdvpn/bd.ovpn ]; then
    echo "trying BD openvpn tunnel (103.163.246.25)..."
    sudo apt-get install -y -q openvpn >/dev/null 2>&1 || true
    sudo openvpn --config guests_manager/bdvpn/bd.ovpn --daemon --log /tmp/vpn.log --verb 3 2>/dev/null || true
    sleep 12
    COUNTRY=$(curl -s --max-time 10 https://ipinfo.io/json 2>/dev/null | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('country'))
except Exception: print('x')" 2>/dev/null)
    echo "tunnel exit country: $COUNTRY"
    if [ "$COUNTRY" = "BD" ]; then
      BD_TUNNEL=1
      echo "BD TUNNEL UP — direct BD exit active"
    else
      sudo pkill openvpn 2>/dev/null || true
      echo "tunnel failed/exit not BD — falling back to direct/proxy"
    fi
  fi
fi
export BD_TUNNEL

# --- BD proxy hunt for the glory engine when tunnel is down ---
BD_PROXY_URL=""
if [ "$GEN" = "True" ] && [ "$BD_TUNNEL" != "1" ]; then
  echo "BD tunnel down — hunting BD proxy for glory engine..."
  BD_PROXY_URL=$(python3 levelbot/bd_proxy.py 2>/dev/null | tail -1)
  if [ -n "$BD_PROXY_URL" ]; then
    echo "BD proxy found: $BD_PROXY_URL"
  else
    echo "no working BD proxy found — engine will try direct login"
  fi
fi
export BD_PROXY_URL

# --- BD guest registration (config: guests_manager/register_config.json) ---
if [ "$REN" = "True" ]; then
  echo "BD register pipeline: enabled"
  (
    BD_TUNNEL=$BD_TUNNEL python3 -u guests_manager/game_register.py --max-hours 2.5 2>&1 | tee register_log.txt
    python3 -u guests_manager/game_register.py --max-hours 2.5 2>&1 | tee register_log.txt
    echo "register pipeline finished"
    tail -5 register_log.txt
  ) &
else
  echo "BD register pipeline: disabled in register_config.json"
fi

# --- glory level engine (config: level_config.json in repo) ---
if [ -f level_config.json ]; then
  ENABLED=$(python3 -c "import json;print(json.load(open('level_config.json')).get('enabled',False))" 2>/dev/null)
  if [ "$ENABLED" = "True" ]; then
    ACC=$(python3 -c "import json;print(json.load(open('level_config.json')).get('accounts',2))" 2>/dev/null)
    CYC=$(python3 -c "import json;print(json.load(open('level_config.json')).get('cycles',5))" 2>/dev/null)
    echo "glory engine: accounts=$ACC cycles=$CYC BD_TUNNEL=$BD_TUNNEL"
    python3 -m levelbot.run_level --all "$ACC" --cycles "$CYC" > farm_log.txt 2>&1 || true
    echo "=== farm log tail ==="
    tail -30 farm_log.txt
  else
    echo "glory engine disabled in level_config.json"
  fi
fi

# wait out the rest of the window
while [ $(date +%s) -lt $END ]; do sleep 30; done
kill $BOT_PID 2>/dev/null

# commit farm progress + log via contents API (USAGE_PAT)
if [ -n "$GITHUB_USAGE_TOKEN" ] && [ -f level_progress.json ]; then
  python3 - <<'PYCOMMIT'
import base64, json, os, urllib.request
TOKEN = os.environ["GITHUB_USAGE_TOKEN"]
def put(path, content, msg):
    api = f"https://api.github.com/repos/selimsb645-max/ff-like-bot/contents/{path}"
    req = urllib.request.Request(api, headers={"Authorization": f"token {TOKEN}", "User-Agent": "glory"})
    try:
        sha = json.load(urllib.request.urlopen(req, timeout=20))["sha"]
    except Exception:
        sha = None
    body = json.dumps({"message": msg, "content": base64.b64encode(content.encode()).decode(), **({"sha": sha} if sha else {}), "branch": "main"}).encode()
    req2 = urllib.request.Request(api, data=body, method="PUT",
        headers={"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "glory"})
    try:
        urllib.request.urlopen(req2, timeout=20)
        print("committed", path)
    except Exception as e:
        print("commit failed", path, e)
put("level_progress.json", open("level_progress.json").read(), "glory farm progress")
try:
    put("farm_log.txt", open("farm_log.txt").read()[-60000:], "glory farm log")
except Exception:
    pass
PYCOMMIT
fi

# self-redispatch next run (job GITHUB_TOKEN can't dispatch; USAGE_PAT can)
if [ -n "$GITHUB_USAGE_TOKEN" ]; then
  curl -s -X POST -H "Authorization: Bearer $GITHUB_USAGE_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    https://api.github.com/repos/selimsb645-max/ff-like-bot/actions/workflows/keep-bot-alive.yml/dispatches \
    -d '{"ref":"main"}' && echo "redispatched next run"
fi
