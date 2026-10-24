#!/bin/bash
# runner: keep bot alive until the job timeout, then exit (workflow re-dispatches itself)
mkdir -p pool
if [ -n "$BD_CREDS_B64" ]; then
  echo "$BD_CREDS_B64" | base64 -d > bd_creds.txt
fi
END=$(( $(date +%s) + 20500 ))  # ~5h42m, under the 6h Actions limit
while [ $(date +%s) -lt $END ]; do
  timeout 5400 python3 -u bot.py
  echo "bot exited, restarting in 10s..."
  sleep 10
done

# self-redispatch next run (job GITHUB_TOKEN can't dispatch; USAGE_PAT can)
if [ -n "$GITHUB_USAGE_TOKEN" ]; then
  curl -s -X POST -H "Authorization: Bearer $GITHUB_USAGE_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    https://api.github.com/repos/selimsb645-max/ff-like-bot/actions/workflows/keep-bot-alive.yml/dispatches \
    -d '{"ref":"main"}' && echo "redispatched next run"
fi
