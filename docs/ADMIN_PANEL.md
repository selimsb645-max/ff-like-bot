# NAI Admin — Super Admin Control Panel

Live at `https://solas-5609a435.base44.app/functions/naiAdmin`

One backend function serves the login page, the panel and a small JSON API.
It reads and writes the same entities as `naiAccess` and `naiBot`, so there is no
second database, no second user table and no second audit trail.

## Why one endpoint with `?r=`

The platform routes `/functions/<name>` only, so `/functions/naiAdmin/api/stats`
is rejected before it reaches the function. Every route therefore travels as the
`r` query parameter:

```
GET  /functions/naiAdmin                 login page, or the panel when signed in
POST ?r=auth/request                     send a 6 digit code on Telegram
POST ?r=auth/verify   { code }           -> signed session cookie
POST ?r=auth/logout
GET  ?r=link&t=<signed>                  signs in and renders the panel
GET  ?r=api/stats | users | users/<tg> | installations | codes | events | settings | me
POST ?r=api/settings  { key, value }
POST ?r=api/action    { action, target, confirm:true }
```

## Authentication

1. The login page asks the server for a code.
2. The server makes a 6 digit code, stores only `SHA-256("8012564523:code")` with
   a 2 minute expiry, and sends the code to the Super Admin through
   `@NAI_AccessBot` (Bot API, chat id 8012564523).
3. Five wrong codes or five requests per 10 minutes per IP are refused.
4. A correct code issues a session token: `base64url(payload).HMAC-SHA256`,
   payload `{tg, role:"superadmin", kind:"session", exp}`.
5. The token travels in `nai_admin` — `HttpOnly; Secure; SameSite=Strict; Max-Age=8h`.
6. Every `/api/*` call re-derives the signature and re-checks `role` and `tg`
   against the constant `SUPER_ADMIN`. Nothing is trusted from the browser.
7. `kind` separates a session from a one-time link, so a link token cannot be
   replayed as a cookie.

The panel has no password, no user list and no second identity store. Losing a
session is free: request a new code.

## API surface

Nothing is written without a session, an `X-Panel: 1` header and `confirm:true`.

| Action | Effect |
|---|---|
| `block` / `unblock` | `NaiUser.blocked` |
| `revoke` | installation revoked + activation REVOKED |
| `reset` | activation REVOKED, installation unbound, next start needs a code |
| `revoke_code` | only for `issued` codes, never a redeemed one |
| `reveal` | returns the full installation id, logs the reveal |

Settings are a fixed registry with server-side bounds:

```
code_ttl_minutes            1..120    code_min_gap_seconds    0..600
code_max_per_day            1..100    max_active_installations 1..20
verify_offline_grace_hours  0..168    ip_change_monitoring     0|1
```

An out of range or unknown key is refused with 400; the value shown in the panel
is the value the server stored.

## Privacy choices

- Installation ids render as `••••••<last 6>`; the full id is only shown after a
  logged `reveal`.
- IPs render as `203.83.160.×`.
- Codes are shown as a short record id. The hash is never returned, and the
  plaintext never exists on the server at all.
- Telegram ids are shown only inside the authenticated panel.

## Tests

```
cd lb && python3 tests/test_admin_panel.py
```

Last run: 44/44 against the deployed function, including forged and expired
signatures, a session minted for another Telegram id, every read endpoint, every
action, and the two existing systems (bot + access API) still answering.
