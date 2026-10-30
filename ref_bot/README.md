# Reference guild bot (subhajit-maji/free-fire-guild-bot-tcp)

The working engine, vendored so the phone can run it directly.

- `app.py` (216 KB) — TCP chat (TcPChaT, 1501 lines) + online (TcPOnLine, 282 lines),
  guild join packets (`create_authenticated_join_packet`, `create_account_join_packet`,
  `request_join_with_badge`), squad invite/accept, likes.
- `xC4.py`, `xHeaders.py` — crypto/header helpers.
- `Pb2/` — protobuf schemas.
- `APIS/insta.py` — helper API module.

## Run on the phone
```
cd ~/ff-like-bot/ref_bot
pip install -r requirements.txt
python3 app.py
```

Set the account to use at the top of `MaiiiinE()` in `app.py` (Uid, Pw).
