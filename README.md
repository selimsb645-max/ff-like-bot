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

## Access control - who can run this bot

The repo is private and the bot is locked twice. Cloning it is not enough.

*Layer 0 (GitHub):* only people you invite can clone. Password authentication is
dead there; they use a fine-grained token (Contents: Read-only) as the password.

*Layer 1 (`auth.py`):* per-person bot login.

    python3 auth.py adduser rahim     # give access
    python3 auth.py list              # who can run it
    python3 auth.py deluser rahim     # take it back

*Layer 2 (`access.py`):* one shared gate password + a personal, single-use,
device-bound access key.

    python3 access.py new rahim       # issue a key, printed once
    python3 access.py list            # state: unused / active / revoked
    python3 access.py revoke rahim    # kill it now
    python3 access.py setgate         # change the shared username/password
    python3 access.py status          # what the gate expects

The start-up prompts, in order:

    Username   : selimsb645-max
    Password   : (the shared gate password)
    Access key : NAI-XXXX-XXXX-XXXX

The key welds itself to the first device that uses it. The same key on another
phone, another Termux install or another account is refused with
"this key is already activated on another device". 3 failed attempts and the
program exits. Servers and Actions skip the prompt only when
`BOT_USERNAME`/`BOT_PASSWORD` (and optionally `BOT_ACCESS_KEY`) are set as
secrets, which are verified against the same hashes.

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
