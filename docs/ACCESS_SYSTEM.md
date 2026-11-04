# NAI Access System

Server-authoritative licensing for the ff-like-bot terminal: the terminal asks,
the server answers, and the answer is the only thing that counts.

## Pieces

| Piece | Where | Role |
|---|---|---|
| Telegram bot | `functions/naiBot.ts` (@NAI_AccessBot) | menu, code generation, status, owner console |
| Access API | `functions/naiAccess.ts` | generate / activate / verify / status / revoke / reset / block / config |
| Tables | `NaiUser`, `NaiInstallation`, `NaiActivation`, `NaiEvent`, `AccessCode` | users, installations, activations, audit trail, code records |
| Terminal client | `lb/nai_client.py` | installation identity + activation |
| Gate | `lb/access.py` → `require_access()` | runs the client before the bot starts |
| Tests | `lb/tests/test_access_system.py`, `lb/tests/test_integration_audit.py` | live checks incl. the step-2 audit |

## Installation identity

`~/.nai/installation.json` holds a random 32-hex `installation_id`, created on
first run, outside the git repository, mode 0600.

- first run: new id → not registered → asks for a code
- later runs: same id → server says ACTIVE → starts silently
- reinstall that loses `~/.nai`: new id → new code required
- `git clone` alone proves nothing, and is never treated as proof

## Flow

1. Person opens @NAI_AccessBot and presses **/start**. The bot shows a menu and
   never issues a code by itself.
2. **🎟 Generate Access Code** → bot checks channel membership, then asks the API.
   The API applies the rate limits, generates a code, stores only its SHA-256,
   sets `expires_at` (default 5 minutes) and returns the plaintext code once.
3. The terminal asks for the code, calls `activate(code, installation_id)`.
4. The API validates, claims the code, and binds one installation to it.
5. Every later start calls `verify(installation_id)`. ACTIVE starts, REVOKED and
   EXPIRED stop. No internet: up to 24 h grace since the last good check.

## Endpoints

All on `POST https://solas-5609a435.base44.app/functions/naiAccess`:

```
generate  { tg_user_id, username?, display_name? }   internal header only
activate  { code, installation_id }
verify    { installation_id }
status    { tg_user_id }
revoke    { actor_tg_id, installation_id | code }    owner
reset     { actor_tg_id, installation_id }           owner
block     { actor_tg_id, tg_user_id, blocked }       owner
config    { actor_tg_id, key, value }                owner
events    { actor_tg_id }                            owner
```

Owner actions are checked against a fixed Telegram id on the server. The
terminal never holds an admin credential.

Configurable settings live in `LibSetting`:

```
code_ttl_minutes          5     code_min_gap_seconds  15
code_max_per_day          10    max_active_installations  3
```

`max_active_installations` replaces the old hard refusal: a person who reinstalls
or moves phone must be able to obtain a code, so the cap is per account (default
3 live installations) instead of "already active, refused". Duplicate generation
is still blocked by the 15 s gap and by the fact that an activated installation
never asks.

## Owner console in Telegram

```
/admin                        totals, TTL, recent activations
/find <tgid>                  one person's picture
/revoke <installation|code>   kill an activation now
/reset <installation>         free it for a new code
/block <tgid> · /unblock      stop/allow code requests
/config <key> <value>         change a setting
/history                      recent codes (never plaintext)
```

## Tests

```
cd lb && python3 tests/test_access_system.py
```

Last run: **15/15 passed** (fresh code, activation, re-run, no extra code,
old-code reuse refused, fresh clone refused, simultaneous redemption single
winner, repeat verification, throttling, revoke, post-revoke refusal, server
record surviving local deletion, admin allowed, admin refused to a stranger).

## Audit trail (NaiEvent)

`code_issued`, `activation`, `ip_change`, `reuse_attempt`, `expired_code`,
`revoked`, `reset`, `blocked`.

An IP change is recorded with the previous address and **never** revokes an
activation, so mobile network and Wi-Fi changes are harmless. Verify:
`/tmp` style check in the audit suite, or `action:"events"` which also returns
`security_events` and `recent_events`.

## Step 2 audit result (2026-11-04)

36 checks run against the live server, client and Telegram webhook; all pass
after two harness bugs were fixed (token parsing of `$'...'` in `.env`, and a
request that was supposed to omit the shared secret but still sent it).

## Known limits (read before promising anything)

- The database has no multi-statement transactions. Redemption is claimed first,
  then re-read after 250 ms; the loser backs out. A writer landing inside that
  window is caught by the read-back. This is a strong practical guard, not a
  database guarantee.
- The platform rate-limits entity calls (70 reads / 60 s per caller), so the API
  does several entity round-trips per request. Pacing matters for bulk testing;
  a single phone checking once per start is far under the limit.
- Installations activated before this system (device-fingerprint keys in
  `access_keys.json`) keep working through the legacy path in `access.py`. They
  move to the installation model the next time they are issued a code.
- IP is recorded as a signal only. It never revokes an activation, so mobile and
  Wi-Fi changes are harmless.
- The bot/API shared secret is a constant in both function sources. It never
  reaches a terminal, but it should move to an environment secret before the
  code is shared with anyone else.
- A code is bound to an installation, not to a Telegram identity: whoever holds a
  live code can redeem it within its 5 minutes. One-use plus the short TTL is the
  mitigation, not a guarantee.
- `verify` returns the owning Telegram user id to whoever presents the
  installation id. The installation id is a 32-hex secret, so this is a bearer
  model: treat it like a password.
- Offline grace is 24 h: revoking a device that never comes back online takes
  effect the next time it does.
