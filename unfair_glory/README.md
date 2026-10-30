# UNFAIR GLORY BOT (complete vendored copy)

Full original package: `app.py`, `main.py`, `dashboard_server.py`, `guild.py`,
all `*_pb2.py` protos (incl. `reqClan_pb2`), `GUEST_GEN_FIXED.py`, dashboard
templates, `devices.json`, `accounts.json`, `jwt_tokens.json` (+ `.bak`),
`token_cache.json`, and the untouched original archive
`UNFAIR_GLORY_BOT_original.zip`.

## Run
```
pip install -r unfair_glory/requirements.txt
cd unfair_glory && python3 app.py
```

## Notes
- ⚠️ This folder holds live guest-account credentials and JWT tokens and the
  repository is public, so anyone with the link can read them. Flip the repo to
  private (or delete these JSON files) if that becomes a problem.
- Two mechanisms from this bot are also applied to our own engine:
  1. TCP auth token: BD prefix `7219` (online) / `8119` (chat) with fixed-width
     fields (uid 16 hex, ts 8 hex, len 8 hex) — the old `0115` variable-width
     token is what made the server close the socket.
  2. Guild join: `POST /RequestJoinClan` with AES-CBC `MyMessage{field_1: clan_id}`
     and `Content-Type: application/octet-stream`.
