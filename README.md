# FF Like Bot
Free Fire BD stats + like automation Telegram bot, self-hosted on GitHub Actions.

Files:
- bot.py: Telegram bot (/stats, /like, /pool)
- ff_bd_api.py: FF BD API client (stats, likes, JWT pool)
- run.sh: runner loop with auto-restart
- .github/workflows/keep-bot-alive.yml: 24/7 keep-alive workflow

Secrets required: BOT_TOKEN (Telegram), BD_CREDS_B64 (base64 of bd_creds.txt)

## Owner's phone as the BD proxy (best setup, free)

Public BD proxies keep dying and get flagged. The owner's own phone on mobile data
gives a real BD residential/mobile IP. GitHub Actions reaches it over Tailscale.

Phone side (one time):
1. Install **Tailscale** (Play Store) and sign in; note the tailnet IP `100.x.x.x`.
2. Install **Termux**, then:
   `pkg update -y && pkg install microsocks -y`
   `termux-wake-lock && microsocks -i 0.0.0.0 -p 1080 &`
3. Keep the phone on mobile data, charging, with battery optimization disabled for
   both apps while farming.

Repo secrets:
- `TAILSCALE_AUTHKEY` — reusable auth key (login.tailscale.com/admin/settings/keys)
- `PHONE_PROXY_IP` — the phone's `100.x.x.x` tailnet IP

Workflow: joins the tailnet, probes `socks5://PHONE_PROXY_IP:1080`, prints the exit
IP/country. run.sh then exports `BD_PROXY_URL` from the phone and skips the public
proxy hunt entirely (auth, mint and the match engine all exit via the phone).
If the phone is offline the run falls back to the old tunnel/proxy hunt.
