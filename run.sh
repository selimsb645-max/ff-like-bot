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
