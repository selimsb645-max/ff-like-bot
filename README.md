# FF Like Bot
Free Fire BD stats + like automation Telegram bot, self-hosted on GitHub Actions.

Files:
- bot.py: Telegram bot (/stats, /like, /pool)
- ff_bd_api.py: FF BD API client (stats, likes, JWT pool)
- run.sh: runner loop with auto-restart
- .github/workflows/keep-bot-alive.yml: 24/7 keep-alive workflow

Secrets required: BOT_TOKEN (Telegram), BD_CREDS_B64 (base64 of bd_creds.txt)

## Login (required before the bot starts)

This repository is locked. Cloning it is not enough to run it: every entry point
(`bot.py`, `main.py`, `app.py`, `auto_start.py`, `levelbot/run_level.py`) asks for
a username and password first.

```
python3 bot.py
=== FF BD bot is locked ===
Username (3 tries left): selim
Password:
[auth] welcome, selim
```

* The check is a salted **PBKDF2-HMAC-SHA256** hash in `auth.json` (260,000
  rounds). The plain text password is never written anywhere.
* Three wrong attempts and the process exits with no bot, no error output that
  helps guessing.
* Change the login any time with `python3 set_login.py` (it asks for the new
  username and password, then rewrites the hash).
* Check the current state with `python3 auth.py status`, or test a login with
  `python3 auth.py check`.
* For servers and CI, skip the prompt by exporting the same credentials:
  `BOT_USERNAME=... BOT_PASSWORD=... python3 bot.py`. GitHub Actions secrets with
  those two names verify against the same hash. When no secrets are set, the
  scheduled runner (`GITHUB_ACTIONS=true`, no terminal) continues so the farm
  never stalls - set the secrets to close that last door as well.

## (old access note)

The repo is private and the bot is locked. Cloning it is not enough: every entry
point asks for a username and password first (`auth.require_auth()`), so only the
people you add can start it.

Give someone access, one person at a time:

```
python3 auth.py adduser ahmed     # asks for a password (6+ characters)
python3 auth.py list              # shows every user and their role
python3 auth.py deluser ahmed     # revoke - locked out on their next start
python3 auth.py passwd ahmed      # rotate one person's password
python3 auth.py check             # try the login yourself
```

Each person has their own username, salt and PBKDF2 hash in `auth.json`. Revoking
one person never touches anyone else. Passwords are never stored in plain text
and cannot be recovered from the file.

Servers and scheduled Actions runs authenticate with `BOT_USERNAME` +
`BOT_PASSWORD` instead of typing at a prompt; both are verified against the same
hashes, and a wrong pair refuses to start. A scheduled Actions job with no
terminal and no secrets keeps running so the farm does not stall, which is why
setting those two secrets is the way to close that door completely.

## Giving someone access - the whole procedure

Two layers exist and nothing else: GitHub decides who may clone, and one access
key decides who may run. This is the complete path.

### Once, for yourself
1. Create a token with write access so you can publish new keys:
   https://github.com/settings/personal-access-tokens/new
   name `ff-owner`, Only select repositories -> `ff-like-bot`,
   Contents: **Read and write**, 30 days. Keep it private.

### For each new person
1. **Issue the key** on your phone, inside the repo folder:

       python3 give_access.py rahim

   It pulls, mints a key, pushes `access_keys.json` and prints the exact message
   to send. (Without that push their clone would reject the key.)
   The first time it asks for the owner token and saves it in ~/.ff_owner_token.

2. **Create their GitHub token** (their door to the code):
   https://github.com/settings/personal-access-tokens/new
   name `ff-rahim`, Only select repositories -> `ff-like-bot`,
   Contents: **Read-only**, 30 days.
   Naming it after them is what lets you see, later, whose token is whose.

3. **Send them four things**: the clone link, the username `selimsb645-max`,
   their token, and the access key from step 1. Their Telegram bot token is
   their own, you never share yours.

### What they do
    git clone https://github.com/selimsb645-max/ff-like-bot.git
    # Username: selimsb645-max   Password: <their token>
    cd ff-like-bot
    pip install -r requirements.txt
    BOT_TOKEN='<their telegram token>' python3 bot.py
    # Access key: NAI-XXXX-XXXX-XXXX   <- from step 1, works once, then locks
    #                                    to their phone

### Taking access back
    python3 access.py revoke rahim
    git add access_keys.json && git commit -m "revoke rahim" && git push

The key dies on their next start after they pull. For an immediate stop without
waiting for them, delete their token (Settings -> Developer settings ->
Fine-grained tokens) so they cannot pull or clone again, and ask them to stop.

### Seeing who has what
    python3 access.py list     # key per person: unused / active / revoked
    https://github.com/settings/tokens?type=beta   # tokens, last used date

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
