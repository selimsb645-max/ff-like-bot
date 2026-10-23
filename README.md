# FF Like Bot
Free Fire BD stats + like automation Telegram bot, self-hosted on GitHub Actions.

Files:
- bot.py: Telegram bot (/stats, /like, /pool)
- ff_bd_api.py: FF BD API client (stats, likes, JWT pool)
- run.sh: runner loop with auto-restart
- .github/workflows/keep-bot-alive.yml: 24/7 keep-alive workflow

Secrets required: BOT_TOKEN (Telegram), BD_CREDS_B64 (base64 of bd_creds.txt)
