# Backend snapshot — deployed state

These are the exact sources deployed on 2026-11-04 (step 4, production hardening).
The live functions are the source of truth; this folder is the rollback path and
the diffable record, because the workspace repository push is blocked by
pre-existing missing LFS objects elsewhere in that tree.

Deployed functions: naiAccess, naiBot, naiAdmin.
New entity: NaiRateLimit (bucket, window_start, count, updated_at).
Secrets required by the platform: NAI_INTERNAL_SECRET, NAI_SESSION_SECRET,
TELEGRAM_BOT_TOKEN_6. Values are never stored here.
