# UNFAIR GLORY BOT (vendored)

Full glory bot: auto-create guest accounts, per-account device fingerprints,
human-like timing (hello delay / packet jitter / keepalive jitter), TCP match
loop, `/RequestJoinClan` guild join, aiohttp dashboard.

Run:
```
pip install -r unfair_glory/requirements.txt
cd unfair_glory && python3 app.py
```

Credentials are NOT committed. Put your own `accounts.json` and
`jwt_tokens.json` here, or mint fresh accounts from the dashboard.

Two mechanisms borrowed from this bot are also patched into `levelbot/`:
1. TCP auth token prefix for BD is `7219` with fixed-width fields (not `0115`).
2. Guild join is `POST /RequestJoinClan` with an AES-CBC `MyMessage{field_1: clan_id}` body.
