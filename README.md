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
If the phone is offline the run falls back to the old tunnel/proxy hunt.## Getting in - clone, then claim one code

### What the person does
    git clone https://github.com/selimsb645-max/ff-like-bot.git
    # Username: selimsb645-max   Password: <the token the owner gave>
    cd ff-like-bot
    pip install -r requirements.txt
    BOT_TOKEN='<their own bot token>' python3 bot.py

    === FF BD bot is locked ===
    1) Telegram খুলে @NAI_AccessBot খোঁজো, Start চাপো
    2) যে কোডটা আসবে সেটা এখানে পেস্ট করো
    Access code (3 tries left): NAI-4K7Q-9XZ2-M3TB

In Telegram the bot first asks them to join https://t.me/FF_FreeLike_BD:
it sends two buttons, "📢 চ্যানেলে জয়েন করো" and "✅ ভেরিফাই". Tapping verify
makes the bot re-check membership with Telegram; only a real member gets the
code, and the message then turns into "✅ ভেরিফাইড".



### Seeing who has a code

    /history              five cards per page, newest first
    /history 2            page two
    /history bound        only the phones that are welded
    /history revoked      only the ones that were switched off

Each code is its own card: the code in bold, who took it with their Telegram id,
which phone it is welded to (first six characters of the fingerprint), when it
was created, when it was welded, the IP it claimed from, and whether it is
🕓 waiting, 🔒 bound or 🚫 revoked. Numbers and times are in Bengali and Dhaka
time. Owner-only, like `/revoke`; anyone else gets "শুধু owner দেখতে পারবে".


## Giving the bot to someone else

Telegram serves **one** `getUpdates` consumer per bot token. Two copies on one
token do not share the load, they kill each other:

    ApiTelegramException ... 409 Conflict: terminated by other getUpdates request

So every person runs **their own bot from @BotFather with their own token**.
`join.py` refuses a token whose username is one of the owner's bots and refuses
a token that some other machine is already polling, so this mistake surfaces at
setup instead of as a traceback later.

### What the owner hands over, per person

1. an invite to the channel the gate checks, so @NAI_AccessBot will issue a code
2. a fine-grained GitHub token: `Contents: Read-only`, expiry 30 days, named
   after the person ("ff-rahim") so revoking one person never touches anyone else
3. this message: "clone the repo with that token, run `python3 join.py`, paste
   your own bot token from @BotFather when it asks"

### What that person does

    git clone https://x-access-token:<their-token>@github.com/selimsb645-max/ff-like-bot.git
    cd ff-like-bot
    pip install -r requirements.txt
    python3 join.py

`join.py` checks the token with getMe, proves nothing else is polling it, writes
it to `.env` (mode 600, git-ignored), runs the access gate so the NAI-... code
binds to that machine's fingerprint, then starts `bot.py`.

### Their phone, their server

* **Termux on their phone** - the natural place: BD mobile IP, so guest-account
  minting works, and nothing depends on GitHub Actions.
* **A VPS they rent** - fine for the Telegram side, but the like pool must still
  be minted from a BD IP. `deploy/ffbot@.service` runs it under systemd:

      sudo cp deploy/ffbot@.service /etc/systemd/system/
      sudo systemctl daemon-reload
      sudo systemctl enable --now ffbot@<name>

* **One VPS for several people** - one directory and one token each, so one
  shared server never means one shared licence:

      sudo ./deploy/add-user.sh rahim --pat ghp_xxx
      sudo -u ffbot bash -c 'cd /srv/ffbot/rahim && python3 join.py --no-start'
      sudo systemctl enable --now ffbot@rahim

  `/srv/ffbot/<name>/.ff_access_device` is that person's licence. Deleting the
  directory, or revoking the code in Telegram, ends their access; nothing else
  is touched.

### Nothing to write down

The code, the device, the IP and the last check all sit in the AccessCode table,
so `/history` in @NAI_AccessBot is the register of who runs what, and `/revoke`
is the off switch. A person who stops paying needs no remote access to their
machine: revoke the code and their next start is refused.

### Who runs what

The owner's own bot is the one on GitHub Actions (`keep-bot-alive.yml`, its own
`BOT_TOKEN` secret, redispatches itself hourly). That copy is never shared: no
other person is ever given that token.

Everyone else clones the repo with their own read-only token, makes their own
bot with @BotFather and runs `join.py` on their own phone or server. Different
tokens mean the two worlds never collide, and neither one needs the other's
GitHub minutes.

The only rule that follows from this: while the Actions copy holds the token,
starting `bot.py` on the owner's phone with that same token will lose to it with
409. Test on the phone with the Action cancelled, or with a second throwaway bot
token.

### The lock screen

The access gate prints in colour on a terminal (grey rule lines, cyan title,
yellow prompt, green when a code is accepted, red when it is not) and in plain
English without colour when the output is piped or `NO_COLOR` is set. The lines
read:

    1  Open Telegram, search @NAI_AccessBot and press START
    2  Paste the code it sends you below

    Access code (3 tries left):

### Revoking someone

The owner writes this to @NAI_AccessBot from his own Telegram account:

    /revoke NAI-4K7Q-9XZ2-M3TB

That flips the code to `revoked` in the AccessCode table. Every start now asks
the server `{action:"check", device}` before the bot runs, so the bound phone is
refused on its **next launch** with "access to this phone was revoked". Nothing
has to change on their phone, and a fresh code is needed to get back in.
Only the owner's Telegram id can revoke; anyone else gets "শুধু owner পারবে".

If the phone has no internet it may still start for up to 24 hours after its
last successful check (so a farm without a link does not die instantly), and it
refuses after that until it reaches the server once.

### Under the hood
* `functions/ffAccessBot` (Base44, webhook of @NAI_AccessBot) mints the code and
  stores it in the `AccessCode` table as `issued`.
* `functions/ffAccessRedeem` is called by access.py with `{code, device}`; it
  welds the code to that device plus the caller's IP, then answers ok/refused.
  Expired, revoked, wrong code, other device, other network each get their own
  message.
* The terminal keeps a small local record (`~/.ff_access_device` +
  `access_keys.json`) so an activated phone starts silently.
* Old `NAI-xxxx-xxxx-xxxx` keys and the offline signed codes still work as
  fallbacks when the server cannot be reached.


