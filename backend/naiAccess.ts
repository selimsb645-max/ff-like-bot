// ============================================================
// NAI ACCESS API — server-authoritative licensing
//
// One endpoint, action-routed:
//   POST /functions/naiAccess   { action: "..." }
//
//   generate  { tg_user_id, username?, display_name? }   -> code (shown once)
//   activate  { code, installation_id }                   -> activation
//   verify    { installation_id }                         -> ACTIVE | PENDING | EXPIRED | REVOKED | NOT_ACTIVATED
//   status    { tg_user_id }                              -> that user's own picture
//   revoke    { actor_tg_id, installation_id | code }      (owner only)
//   reset     { actor_tg_id, installation_id }             (owner only)
//   block     { actor_tg_id, tg_user_id, blocked }         (owner only)
//   config    { actor_tg_id, key, value }                  (owner only)
//
// Rules that matter:
//   * the code itself is never stored, only its SHA-256
//   * one code activates exactly one installation, once
//   * the server decides, the terminal only asks
// ============================================================
import { createClientFromRequest } from "npm:@base44/sdk@0.8.54";

const OWNER_ID = "8012564523";
const DEFAULT_TTL_MIN = 5;
const MIN_GAP_SEC = 15;          // between two code requests by the same user
const MAX_PER_DAY = 10;          // codes per user per day
// Environment-only. The hardcoded fallback was removed after the platform
// secret was confirmed present, so a missing secret fails closed instead of
// silently accepting a value that is written in the repository.
const INTERNAL = Deno.env.get("NAI_INTERNAL_SECRET") ?? "";
const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientIp(req: Request): string {
  const h = req.headers;
  return (h.get("x-forwarded-for") || h.get("x-real-ip") || h.get("cf-connecting-ip") || "")
    .split(",")[0].trim();
}

const rows = (p: any): any[] => p?.items ?? p ?? [];

async function sha256(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function randHex(bytes: number): string {
  return [...crypto.getRandomValues(new Uint8Array(bytes))]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

function block4(): string {
  const b = crypto.getRandomValues(new Uint8Array(4));
  let out = "";
  for (let i = 0; i < 4; i++) out += ALPHABET[b[i] % ALPHABET.length];
  return out;
}

const newCode = () => `NAI-${block4()}-${block4()}-${block4()}`;
const norm = (s: unknown) => String(s ?? "").trim().toUpperCase();
const dayKey = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Dhaka" });

async function setting(svc: any, key: string, fallback: string): Promise<string> {
  try {
    const found = rows(await svc.entities.LibSetting.filter({ key }, { limit: 1 }))[0];
    return found?.value ? String(found.value) : fallback;
  } catch (_e) {
    return fallback;
  }
}

/**
 * Shared, persistent rate limiting.
 *
 * Counters live in NaiRateLimit so every instance of this function reads the
 * same number; an in-memory Map would reset on cold start and would only cover
 * one instance. The platform offers no compare-and-swap on a single row, so a
 * burst of simultaneous *first* requests can each read the same value and let a
 * few extra calls through. That is a bounded overcount, and this is a misuse
 * brake, not an authorization control: authorization stays in the internal
 * signature and the installation binding. If the counter store fails the
 * request is allowed and the failure is not fatal, because a broken counter
 * must never lock every honest user out.
 */
async function limit(svc: any, bucket: string, max: number, windowSec: number) {
  const nowSec = Math.floor(Date.now() / 1000);
  const start = Math.floor(nowSec / windowSec) * windowSec;
  const retry = start + windowSec - nowSec;
  try {
    const row = rows(await svc.entities.NaiRateLimit.filter({ bucket, window_start: start }, { limit: 1 }))[0];
    if (row) {
      const count = Number(row.count ?? 0) + 1;
      await svc.entities.NaiRateLimit.update(row.id, { count, updated_at: new Date().toISOString() });
      return { ok: count <= max, count, retry, degraded: false };
    }
    await svc.entities.NaiRateLimit.create({
      bucket, window_start: start, count: 1, updated_at: new Date().toISOString(),
    });
    return { ok: true, count: 1, retry, degraded: false };
  } catch (_e) {
    return { ok: true, count: 0, retry, degraded: true };
  }
}

async function upsertUser(svc: any, tgId: string, username: string, display: string, ip: string) {
  const user = rows(await svc.entities.NaiUser.filter({ telegram_user_id: tgId }, { limit: 1 }))[0];
  if (user) {
    const patch: any = {};
    if (username && username !== user.username) patch.username = username;
    if (display && display !== user.display_name) patch.display_name = display;
    if (Object.keys(patch).length) await svc.entities.NaiUser.update(user.id, patch);
    return { ...user, ...patch };
  }
  const made = await svc.entities.NaiUser.create({
    telegram_user_id: tgId,
    username,
    display_name: display,
    blocked: false,
    created_at: new Date().toISOString(),
    codes_today: 0,
    codes_day: dayKey(),
    note: ip ? `first seen from ${ip}` : "",
  });
  return made;
}

/** Audit trail. A failed audit write must never break the request itself. */
async function logEvent(svc: any, event_type: string, install: string, tg: string, ip: string, detail: string) {
  try {
    await svc.entities.NaiEvent.create({
      event_type,
      installation_id: install || "",
      telegram_user_id: tg || "",
      ip: ip || "",
      detail: detail || "",
    });
  } catch (_e) { /* audit is best effort */ }
}

async function findByCode(svc: any, code: string) {
  const hash = await sha256(code);
  const byHash = rows(await svc.entities.AccessCode.filter({ code_hash: hash }, { limit: 5 }))[0];
  if (byHash) return byHash;
  // rows issued before hashes existed
  return rows(await svc.entities.AccessCode.filter({ code }, { limit: 5 }))[0];
}

async function isExpired(rec: any, ttlMin: number): Promise<boolean> {
  const stamp = rec.expires_at || "";
  if (stamp) return Date.now() > new Date(stamp).getTime();
  const issued = rec.issued_at || rec.created_date || "";
  if (!issued) return false;
  return Date.now() > new Date(issued).getTime() + ttlMin * 60000;
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ ok: false, error: "POST only" }, 405);

  let body: any = {};
  try {
    body = await req.json();
  } catch {
    return json({ ok: false, error: "bad json" }, 400);
  }

  const action = String(body.action ?? "").toLowerCase();
  const svc = createClientFromRequest(req).asServiceRole;
  const ip = clientIp(req);
  const now = new Date().toISOString();

  // ---------- generate ----------
  if (action === "generate") {
    if (!INTERNAL || req.headers.get("x-nai-internal") !== INTERNAL) {
      if (!INTERNAL) await logEvent(svc, "admin_action", "", "", ip, "server has no NAI_INTERNAL_SECRET configured");
      return json({ ok: false, error: "unauthorized" }, 401);
    }
    const tgId = String(body.tg_user_id ?? "").trim();
    if (!tgId) return json({ ok: false, error: "tg_user_id is required" }, 400);

    const perIp = await limit(svc, `gen-ip-${ip || "unknown"}`, 20, 3600);
    if (!perIp.ok) {
      return json({ ok: false, status: "TOO_MANY",
        message: `too many codes from this address, retry in ${perIp.retry}s` }, 429);
    }
    const perUser = await limit(svc, `gen-user-${tgId}`, 12, 3600);
    if (!perUser.ok) {
      return json({ ok: false, status: "TOO_MANY",
        message: `too many codes for this account, retry in ${perUser.retry}s` }, 429);
    }

    const user = await upsertUser(svc, tgId, String(body.username ?? ""), String(body.display_name ?? ""), ip);
    if (user.blocked === true) {
      return json({ ok: false, status: "BLOCKED", message: "this account is blocked" }, 403);
    }

    // one live activation already covers this person: no new code needed
    // An activated installation never asks again, so a request here means a
    // different phone, a re-clone or a reset installation. Refusing outright
    // would lock that person out, so the cap is per account instead.
    const mine = rows(await svc.entities.NaiInstallation.filter({ telegram_user_id: tgId }, { limit: 20 }))
      .filter((r: any) => r.status === "active");
    const maxInstalls = Number(await setting(svc, "max_active_installations", "3")) || 3;
    if (mine.length >= maxInstalls) {
      return json({
        ok: false, status: "LIMIT_INSTALLATIONS",
        message: `this account already has ${mine.length} active installations (limit ${maxInstalls}) - ask the owner for a reset`,
        installations: mine.length,
      }, 409);
    }

    const ttl = Number(await setting(svc, "code_ttl_minutes", String(DEFAULT_TTL_MIN))) || DEFAULT_TTL_MIN;
    const gap = Number(await setting(svc, "code_min_gap_seconds", String(MIN_GAP_SEC))) || MIN_GAP_SEC;
    const cap = Number(await setting(svc, "code_max_per_day", String(MAX_PER_DAY))) || MAX_PER_DAY;

    if (user.last_code_at) {
      const waited = (Date.now() - new Date(user.last_code_at).getTime()) / 1000;
      if (waited < gap) {
        return json({ ok: false, status: "TOO_SOON", wait_seconds: Math.ceil(gap - waited),
          message: `please wait ${Math.ceil(gap - waited)}s before asking again` }, 429);
      }
    }
    const today = dayKey();
    const usedToday = user.codes_day === today ? Number(user.codes_today ?? 0) : 0;
    if (usedToday >= cap) {
      return json({ ok: false, status: "LIMIT", message: "daily code limit reached" }, 429);
    }

    const code = newCode();
    const codeHash = await sha256(code);
    const rec = await svc.entities.AccessCode.create({
      code: "",
      code_hash: codeHash,
      status: "issued",
      tg_id: tgId,
      tg_name: String(body.display_name ?? ""),
      device: "",
      installation_id: "",
      activation_id: "",
      ip,
      issued_at: now,
      bound_at: "",
      used_at: "",
      expires_at: new Date(Date.now() + ttl * 60000).toISOString(),
      uses: 0,
      note: "v2 generated",
    });
    await svc.entities.NaiUser.update(user.id, {
      last_code_at: now,
      codes_day: today,
      codes_today: usedToday + 1,
    });
    await logEvent(svc, "code_issued", "", tgId, ip, `code ${code.slice(0, 8)}… ttl ${ttl}m`);
    return json({ ok: true, code, code_id: rec.id, ttl_minutes: ttl, expires_at: rec.expires_at,
      active_installations: mine.length });
  }

  // ---------- activate ----------
  if (action === "activate") {
    const code = norm(body.code);
    const install = String(body.installation_id ?? "").trim();
    if (!code) return json({ ok: false, status: "INVALID", message: "code is required" }, 400);
    if (!/^[a-f0-9]{16,64}$/i.test(install)) {
      return json({ ok: false, status: "INVALID", message: "installation_id must be a random hex id" }, 400);
    }

    const perIp = await limit(svc, `act-ip-${ip || "unknown"}`, 60, 3600);
    if (!perIp.ok) {
      return json({ ok: false, status: "TOO_MANY",
        message: `too many activation attempts, retry in ${perIp.retry}s` }, 429);
    }

    const ttl = Number(await setting(svc, "code_ttl_minutes", String(DEFAULT_TTL_MIN))) || DEFAULT_TTL_MIN;
    const rec = await findByCode(svc, code);
    if (!rec) return json({ ok: false, status: "UNKNOWN", message: "this code was never issued" }, 404);
    if (rec.status === "revoked") {
      return json({ ok: false, status: "REVOKED", message: "this code was revoked" }, 403);
    }

    // already activated on this very installation -> idempotent
    const existing = rows(await svc.entities.NaiInstallation.filter({ installation_id: install }, { limit: 1 }))[0];
    if (existing?.status === "active") {
      const act = rows(await svc.entities.NaiActivation.filter({ activation_id: existing.activation_id }, { limit: 1 }))[0];
      if (act && act.status === "ACTIVE") {
        return json({ ok: true, status: "ACTIVE", reused: true, activation: act });
      }
    }
    if (existing?.status === "revoked") {
      return json({ ok: false, status: "REVOKED",
        message: "this installation was revoked by the owner" }, 403);
    }

    if (rec.installation_id && rec.installation_id !== install) {
      return json({ ok: false, status: "ALREADY_USED",
        message: "this code was already used on another installation" }, 409);
    }
    if (rec.used_at && rec.status === "bound") {
      // A retry after a dropped response must not be punished: same code, same
      // installation -> hand back the activation that already exists.
      if (rec.installation_id === install) {
        const same = rows(await svc.entities.NaiActivation.filter({ activation_id: rec.activation_id }, { limit: 1 }))[0];
        if (same) return json({ ok: true, status: "ACTIVE", reused: true, activation: same });
      }
      return json({ ok: false, status: "ALREADY_USED", message: "this code was already used" }, 409);
    }
    if (await isExpired(rec, ttl)) {
      if (rec.status === "issued") await svc.entities.AccessCode.update(rec.id, { status: "expired" });
      await logEvent(svc, "expired_code", install, String(rec.tg_id ?? ""), ip, "activation attempted with an expired code");
      return json({ ok: false, status: "EXPIRED", message: "this code expired - request a new one" }, 410);
    }

    const activationId = randHex(16);
    const tgId = String(rec.tg_id ?? "");

    // claim the code first, then settle briefly and confirm we are the winner:
    // two racing installations both write, the last write wins, and the loser
    // sees a different installation_id here and backs out.
    await svc.entities.AccessCode.update(rec.id, {
      status: "bound",
      used_at: now,
      bound_at: now,
      installation_id: install,
      activation_id: activationId,
      device: String(body.device ?? ""),
      ip,
      uses: Number(rec.uses ?? 0) + 1,
    });
    await new Promise((r) => setTimeout(r, 400));
    const after = rows(await svc.entities.AccessCode.filter({ id: rec.id }, { limit: 1 }))[0];
    if (after && after.installation_id && after.installation_id !== install) {
      await logEvent(svc, "reuse_attempt", install, tgId, ip, "lost the redemption race for this code");
      return json({ ok: false, status: "ALREADY_USED",
        message: "this code was claimed by another installation" }, 409);
    }
    // Second sample before the activation row is written: the winner's write has
    // had time to become visible, and a second lost race is still caught.
    await new Promise((r) => setTimeout(r, 200));
    const confirm = rows(await svc.entities.AccessCode.filter({ id: rec.id }, { limit: 1 }))[0];
    if (confirm && confirm.installation_id && confirm.installation_id !== install) {
      await logEvent(svc, "reuse_attempt", install, tgId, ip, "lost the redemption race at the second check");
      return json({ ok: false, status: "ALREADY_USED",
        message: "this code was claimed by another installation" }, 409);
    }

    const activation = await svc.entities.NaiActivation.create({
      activation_id: activationId,
      installation_id: install,
      telegram_user_id: tgId,
      code_id: rec.id,
      activated_at: now,
      expires_at: "",
      status: "ACTIVE",
      revoked_at: "",
      last_verified: now,
      last_ip: ip,
    });

    if (existing) {
      await svc.entities.NaiInstallation.update(existing.id, {
        activation_id: activationId,
        telegram_user_id: tgId || existing.telegram_user_id,
        status: "active",
        last_seen: now,
        last_known_ip: ip,
        note: String(body.app_version ?? existing.note ?? ""),
      });
    } else {
      await svc.entities.NaiInstallation.create({
        installation_id: install,
        telegram_user_id: tgId,
        activation_id: activationId,
        status: "active",
        created_at: now,
        last_seen: now,
        last_known_ip: ip,
        app_version: String(body.app_version ?? ""),
        note: "",
      });
    }

    await logEvent(svc, "activation", install, tgId, ip, "installation activated");
    return json({ ok: true, status: "ACTIVE", activation });
  }

  // ---------- verify ----------
  if (action === "verify") {
    const install = String(body.installation_id ?? "").trim();
    if (!install) return json({ ok: false, status: "NOT_ACTIVATED", message: "no installation id" }, 400);

    const perIp = await limit(svc, `verify-ip-${ip || "unknown"}`, 600, 3600);
    if (!perIp.ok) {
      return json({ ok: false, status: "TOO_MANY",
        message: `too many verification calls, retry in ${perIp.retry}s` }, 429);
    }

    const inst = rows(await svc.entities.NaiInstallation.filter({ installation_id: install }, { limit: 1 }))[0];
    if (!inst) return json({ ok: false, status: "NOT_ACTIVATED", message: "this installation is not registered" }, 404);
    if (inst.status === "revoked") {
      return json({ ok: false, status: "REVOKED", message: "access for this installation was revoked" }, 403);
    }

    const act = rows(await svc.entities.NaiActivation.filter({ activation_id: inst.activation_id }, { limit: 1 }))[0];
    if (!act) return json({ ok: false, status: "NOT_ACTIVATED", message: "no activation on record" }, 404);
    if (act.status === "REVOKED") {
      return json({ ok: false, status: "REVOKED", message: "this activation was revoked" }, 403);
    }
    if (act.expires_at && Date.now() > new Date(act.expires_at).getTime()) {
      await svc.entities.NaiActivation.update(act.id, { status: "EXPIRED" });
      return json({ ok: false, status: "EXPIRED", message: "this activation expired" }, 403);
    }

    if (ip && inst.last_known_ip && ip !== inst.last_known_ip) {
      // mobile networks and Wi-Fi change all the time: this is a signal, not a
      // reason to revoke. The activation stays valid.
      await logEvent(svc, "ip_change", install, String(act.telegram_user_id ?? ""), ip,
        `was ${inst.last_known_ip}`);
    }
    await svc.entities.NaiActivation.update(act.id, { last_verified: now, last_ip: ip });
    await svc.entities.NaiInstallation.update(inst.id, { last_seen: now, last_known_ip: ip });
    // Privacy: the owning Telegram id stays server-side. A terminal only needs
    // the verdict, its own activation handle and the timestamps it caches.
    return json({ ok: true, status: "ACTIVE", activation_id: act.activation_id,
      activated_at: act.activated_at, expires_at: act.expires_at || "" });
  }

  // ---------- status ----------
  if (action === "status") {
    const tgId = String(body.tg_user_id ?? "").trim();
    if (!tgId) return json({ ok: false, error: "tg_user_id is required" }, 400);

    const user = rows(await svc.entities.NaiUser.filter({ telegram_user_id: tgId }, { limit: 1 }))[0];
    const installs = rows(await svc.entities.NaiInstallation.filter({ telegram_user_id: tgId }, { limit: 20 }));
    const out: any[] = [];
    for (const inst of installs) {
      const act = rows(await svc.entities.NaiActivation.filter({ activation_id: inst.activation_id }, { limit: 1 }))[0];
      let state = "PENDING";
      if (inst.status === "revoked" || act?.status === "REVOKED") state = "REVOKED";
      else if (act?.status === "ACTIVE") state = "ACTIVE";
      else if (act?.status === "EXPIRED") state = "EXPIRED";
      out.push({
        installation: `…${String(inst.installation_id).slice(-6)}`,
        state,
        activated_at: act?.activated_at ?? "",
        expires_at: act?.expires_at ?? "",
        last_verified: act?.last_verified ?? "",
        bound_ip: inst.last_known_ip ? "bound" : "",
      });
    }
    return json({
      ok: true,
      telegram_user_id: tgId,
      blocked: user?.blocked === true,
      status: out.length ? (out.find((x) => x.state === "ACTIVE") ? "ACTIVE" : out[0].state) : "NOT_ACTIVATED",
      installations: out,
    });
  }

  // ---------- owner-only ----------
  const ownerActions = ["revoke", "reset", "block", "config", "events"];
  if (ownerActions.includes(action)) {
    if (String(body.actor_tg_id ?? "") !== OWNER_ID) {
      return json({ ok: false, error: "owner only" }, 403);
    }

    if (action === "revoke") {
      const install = String(body.installation_id ?? "").trim();
      const code = norm(body.code);
      let inst = install
        ? rows(await svc.entities.NaiInstallation.filter({ installation_id: install }, { limit: 1 }))[0]
        : undefined;
      if (!inst && code) {
        const rec = await findByCode(svc, code);
        if (rec) {
          inst = rows(await svc.entities.NaiInstallation.filter({ installation_id: rec.installation_id || "" }, { limit: 1 }))[0];
          if (rec.installation_id) await svc.entities.AccessCode.update(rec.id, { status: "revoked" });
        }
      }
      if (!inst) return json({ ok: false, status: "NOT_FOUND", message: "nothing to revoke" }, 404);
      await svc.entities.NaiInstallation.update(inst.id, { status: "revoked" });
      const act = rows(await svc.entities.NaiActivation.filter({ activation_id: inst.activation_id }, { limit: 1 }))[0];
      if (act) await svc.entities.NaiActivation.update(act.id, { status: "REVOKED", revoked_at: now });
      await logEvent(svc, "revoked", String(inst.installation_id ?? ""), String(inst.telegram_user_id ?? ""), ip, "revoked by the owner");
      return json({ ok: true, status: "REVOKED", installation: `…${String(inst.installation_id).slice(-6)}` });
    }

    if (action === "reset") {
      const install = String(body.installation_id ?? "").trim();
      const inst = rows(await svc.entities.NaiInstallation.filter({ installation_id: install }, { limit: 1 }))[0];
      if (!inst) return json({ ok: false, status: "NOT_FOUND" }, 404);
      const act = rows(await svc.entities.NaiActivation.filter({ activation_id: inst.activation_id }, { limit: 1 }))[0];
      if (act) await svc.entities.NaiActivation.update(act.id, { status: "REVOKED", revoked_at: now });
      await svc.entities.NaiInstallation.update(inst.id, { status: "reset", activation_id: "" });
      await logEvent(svc, "reset", String(inst.installation_id ?? ""), String(inst.telegram_user_id ?? ""), ip, "reset by the owner");
      return json({ ok: true, status: "RESET", message: "the next run will need a new code" });
    }

    if (action === "block") {
      const tgId = String(body.tg_user_id ?? "").trim();
      const want = body.blocked !== false;
      const user = rows(await svc.entities.NaiUser.filter({ telegram_user_id: tgId }, { limit: 1 }))[0];
      if (!user) return json({ ok: false, status: "NOT_FOUND" }, 404);
      await svc.entities.NaiUser.update(user.id, { blocked: want });
      await logEvent(svc, "blocked", "", tgId, ip, want ? "user blocked" : "user unblocked");
      return json({ ok: true, tg_user_id: tgId, blocked: want });
    }

    if (action === "config") {
      const key = String(body.key ?? "").trim();
      const value = String(body.value ?? "").trim();
      if (!key) return json({ ok: false, error: "key is required" }, 400);
      const found = rows(await svc.entities.LibSetting.filter({ key }, { limit: 1 }))[0];
      if (found) await svc.entities.LibSetting.update(found.id, { value });
      else await svc.entities.LibSetting.create({ key, value });
      return json({ ok: true, key, value });
    }

    if (action === "events") {
      const users = rows(await svc.entities.NaiUser.list("-created_date", 200));
      const installs = rows(await svc.entities.NaiInstallation.list("-created_date", 200));
      const acts = rows(await svc.entities.NaiActivation.list("-created_date", 200));
      const evts = rows(await svc.entities.NaiEvent.list("-created_date", 200));
      return json({
        security_events: evts.length,
        recent_events: evts.slice(0, 10).map((e: any) => ({
          type: e.event_type, at: e.created_date, installation: `…${String(e.installation_id ?? "").slice(-6)}`,
          tg: e.telegram_user_id, detail: e.detail,
        })),
        ok: true,
        users: users.length,
        blocked: users.filter((u: any) => u.blocked === true).length,
        installations: installs.length,
        active: installs.filter((i: any) => i.status === "active").length,
        revoked: installs.filter((i: any) => i.status === "revoked").length,
        activations: acts.length,
        recent: acts.slice(0, 10).map((a: any) => ({
          installation: `…${String(a.installation_id ?? "").slice(-6)}`,
          tg: a.telegram_user_id, at: a.activated_at, status: a.status,
        })),
      });
    }
  }

  return json({ ok: false, error: `unknown action "${action}"` }, 400);
});
