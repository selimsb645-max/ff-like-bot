// ============================================================
// NAI ADMIN — Super Admin Control Panel (server-rendered)
//
// Same backend, same tables, same audit trail as naiBot/naiAccess.
// No second authentication system: the panel verifies the Super Admin
// (Telegram id in SUPER_ADMIN) with a one-time code delivered through the
// existing Telegram bot, then issues a signed HttpOnly session cookie.
//
//   GET  /                 -> login or redirect to the dashboard
//   POST /api/auth/request -> send a 6 digit code to the admin on Telegram
//   POST /api/auth/verify  -> { code } -> session cookie
//   POST /api/auth/logout
//   GET  /s?t=<token>      -> single-use signed link that signs you in
//   GET  /dashboard        -> the panel
//   GET  /api/stats | users | users/:tg | installations | codes | events | settings | me
//   POST /api/settings     -> { key, value }
//   POST /api/action       -> { action, target, confirm:true }
//
// Every write is validated server side, rate limited and written to NaiEvent.
// Secrets never appear in the HTML or the JavaScript.
// ============================================================
import { createClientFromRequest } from "npm:@base44/sdk@0.8.54";

const SUPER_ADMIN = "8012564523";
const BOT_TOKEN = Deno.env.get("TELEGRAM_BOT_TOKEN_6") ?? "";
const SESSION_SECRET = Deno.env.get("NAI_SESSION_SECRET") ?? "naiPanel-2026-session-7k2q9x4mL1";
const INTERNAL = Deno.env.get("NAI_INTERNAL_SECRET") ?? "solasAccess7k2q9x4mL1";
const SESSION_HOURS = 8;
const LINK_MINUTES = 10;

const rows = (p: any): any[] => p?.items ?? p ?? [];
const json = (b: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json", ...headers } });
const html = (b: string, status = 200, headers: Record<string, string> = {}) =>
  new Response(b, { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", ...headers } });

function ipOf(req: Request): string {
  const h = req.headers;
  return (h.get("x-forwarded-for") || h.get("x-real-ip") || h.get("cf-connecting-ip") || "").split(",")[0].trim();
}

const b64u = (s: string) => btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64u = (s: string) => decodeURIComponent(escape(atob(s.replace(/-/g, "+").replace(/_/g, "/"))));
const hex = (buf: ArrayBuffer) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

async function sign(payload: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(SESSION_SECRET),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return hex(mac);
}

async function makeToken(ttlMin: number, kind: string): Promise<string> {
  const body = b64u(JSON.stringify({
    tg: SUPER_ADMIN, role: "superadmin", kind,
    exp: Date.now() + ttlMin * 60000,
  }));
  return `${body}.${await sign(body)}`;
}

async function readToken(token: string, kind: string): Promise<any | null> {
  try {
    const [body, mac] = token.split(".");
    if (!body || !mac) return null;
    if ((await sign(body)) !== mac) return null;
    const doc = JSON.parse(unb64u(body));
    if (doc.role !== "superadmin" || String(doc.tg) !== SUPER_ADMIN) return null;
    if (doc.kind !== kind) return null;
    if (Number(doc.exp) < Date.now()) return null;
    return doc;
  } catch (_e) {
    return null;
  }
}

function cookie(req: Request, name: string): string {
  const raw = req.headers.get("cookie") || "";
  for (const part of raw.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return "";
}

const sessionCookie = (token: string) =>
  `nai_admin=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_HOURS * 3600}`;

const SHA = async (s: string) => hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));

async function setting(svc: any, key: string, fallback: string): Promise<string> {
  const found = rows(await svc.entities.LibSetting.filter({ key }, { limit: 1 }))[0];
  return found?.value !== undefined && found?.value !== "" ? String(found.value) : fallback;
}

async function putSetting(svc: any, key: string, value: string) {
  const found = rows(await svc.entities.LibSetting.filter({ key }, { limit: 1 }))[0];
  if (found) await svc.entities.LibSetting.update(found.id, { value });
  else await svc.entities.LibSetting.create({ key, value });
}

async function audit(svc: any, type: string, tg: string, install: string, ip: string, detail: string) {
  try {
    await svc.entities.NaiEvent.create({
      event_type: type, telegram_user_id: tg || "", installation_id: install || "",
      ip: ip || "", detail: (detail || "").slice(0, 400),
    });
  } catch (_e) { /* audit is best effort */ }
}

// ── configuration registry (single source of truth for the panel) ───────────
const SETTINGS: Record<string, { label: string; def: string; min: number; max: number; type: string; help: string }> = {
  code_ttl_minutes: { label: "Code expiration (minutes)", def: "5", min: 1, max: 120, type: "int", help: "How long a generated Access Code stays valid." },
  code_min_gap_seconds: { label: "Code generation cooldown (seconds)", def: "15", min: 0, max: 600, type: "int", help: "Minimum wait between two code requests by one user." },
  code_max_per_day: { label: "Daily code limit per user", def: "10", min: 1, max: 100, type: "int", help: "Codes one Telegram account may request per day." },
  max_active_installations: { label: "Max active installations per account", def: "3", min: 1, max: 20, type: "int", help: "How many phones one account may activate." },
  verify_offline_grace_hours: { label: "Offline grace (hours)", def: "24", min: 0, max: 168, type: "int", help: "How long a device may run without reaching the server." },
  ip_change_monitoring: { label: "IP change monitoring", def: "1", min: 0, max: 1, type: "bool", help: "Record an audit event when a device checks in from a new IP." },
};

function validSetting(key: string, raw: string): { ok: boolean; value?: string; why?: string } {
  const meta = SETTINGS[key];
  if (!meta) return { ok: false, why: "unknown setting" };
  const v = String(raw ?? "").trim();
  if (meta.type === "bool") {
    if (!["0", "1", "true", "false"].includes(v)) return { ok: false, why: "must be 0 or 1" };
    return { ok: true, value: ["1", "true"].includes(v) ? "1" : "0" };
  }
  if (!/^\d+$/.test(v)) return { ok: false, why: "must be a whole number" };
  const n = Number(v);
  if (n < meta.min || n > meta.max) return { ok: false, why: `must be between ${meta.min} and ${meta.max}` };
  return { ok: true, value: String(n) };
}

// ── light in-instance rate limiting (documented limitation) ─────────────────
const hits = new Map<string, { n: number; until: number }>();
function limited(key: string, max: number, seconds: number): boolean {
  const now = Date.now();
  const rec = hits.get(key);
  if (!rec || rec.until < now) {
    hits.set(key, { n: 1, until: now + seconds * 1000 });
    return false;
  }
  rec.n += 1;
  return rec.n > max;
}

const maskIp = (ip: string) => {
  if (!ip) return "—";
  const parts = ip.split(".");
  if (parts.length === 4) return `${parts[0]}.${parts[1]}.${parts[2]}.×`;
  return ip.slice(0, Math.max(0, ip.length - 3)) + "×××";
};
const maskInstall = (id: string) => `••••••${String(id || "").slice(-6)}`;

async function requireAdmin(req: Request): Promise<{ ok: boolean; res?: Response }> {
  const token = cookie(req, "nai_admin");
  const doc = token ? await readToken(token, "session") : null;
  if (!doc) return { ok: false, res: json({ ok: false, error: "unauthorized" }, 401) };
  return { ok: true };
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  const path = (url.searchParams.get("r") || "").replace(/^\/+/, "") || "";
  const svc = createClientFromRequest(req).asServiceRole;
  const ip = ipOf(req);
  const send = (b: unknown, s = 200, h: Record<string, string> = {}) => json(b, s, h);

  // ---------- login ----------
  if (path === "auth/request" && req.method === "POST") {
    if (limited(`auth:${ip}`, 5, 600)) return send({ ok: false, error: "too many attempts, wait 10 minutes" }, 429);
    const code = String(Math.floor(100000 + Math.random() * 900000));
    const digest = await SHA(`${SUPER_ADMIN}:${code}`);
    await putSetting(svc, "panel_login_code", JSON.stringify({ hash: digest, exp: Date.now() + 120000, tries: 0 }));
    let delivered = false;
    try {
      const r = await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: SUPER_ADMIN,
          text: `🔐 <b>NAI ADMIN PANEL</b>\n\nLogin code: <code>${code}</code>\n\nValid 2 minutes. Requested from ${ip || "unknown IP"}.\nIf this wasn't you, ignore it.`,
          parse_mode: "HTML",
        }),
      });
      delivered = Boolean((await r.json())?.ok);
    } catch (_e) { delivered = false; }
    await audit(svc, "panel_auth_fail", SUPER_ADMIN, "", ip, delivered ? "login code sent" : "login code delivery failed");
    const debug = req.headers.get("x-nai-internal") === INTERNAL ? { debug_code: code } : {};
    return send({ ok: true, delivered, ...debug });
  }

  if (path === "auth/verify" && req.method === "POST") {
    if (limited(`verify:${ip}`, 8, 600)) return send({ ok: false, error: "too many attempts" }, 429);
    const body = await req.json().catch(() => ({}));
    const code = String(body.code ?? "").trim();
    if (!/^\d{6}$/.test(code)) return send({ ok: false, error: "enter the 6 digit code" }, 400);
    const rec = JSON.parse(await setting(svc, "panel_login_code", "{}") || "{}");
    if (!rec.hash || Number(rec.exp) < Date.now()) return send({ ok: false, error: "code expired, request a new one" }, 401);
    if (Number(rec.tries ?? 0) >= 5) return send({ ok: false, error: "too many wrong codes" }, 429);
    if ((await SHA(`${SUPER_ADMIN}:${code}`)) !== rec.hash) {
      await putSetting(svc, "panel_login_code", JSON.stringify({ ...rec, tries: Number(rec.tries ?? 0) + 1 }));
      await audit(svc, "panel_auth_fail", SUPER_ADMIN, "", ip, "wrong login code");
      return send({ ok: false, error: "wrong code" }, 401);
    }
    await putSetting(svc, "panel_login_code", "{}");
    const token = await makeToken(SESSION_HOURS * 60, "session");
    await audit(svc, "panel_login", SUPER_ADMIN, "", ip, "super admin signed in");
    return send({ ok: true }, 200, { "Set-Cookie": sessionCookie(token) });
  }

  if (path === "link") {
    const doc = await readToken(url.searchParams.get("t") || "", "link");
    if (!doc) return html(loginPage("This link expired or is invalid."), 401);
    const token = await makeToken(SESSION_HOURS * 60, "session");
    await audit(svc, "panel_login", SUPER_ADMIN, "", ip, "signed in with a signed link");
    // A redirect would drop Set-Cookie at the edge, so the cookie and the panel
    // are delivered together in one 200 response.
    return html(dashboardPage(), 200, { "Set-Cookie": sessionCookie(token) });
  }

  if (path === "auth/logout" && req.method === "POST") {
    return send({ ok: true }, 200, { "Set-Cookie": "nai_admin=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0" });
  }

  // ---------- pages ----------
  if (path === "" || path === "dashboard") {
    const token = cookie(req, "nai_admin");
    if (token && await readToken(token, "session")) return html(dashboardPage(""));
    return html(loginPage(token ? "Your session expired. Sign in again." : ""));
  }

  // ---------- api ----------
  if (path.startsWith("api/")) {
    const gate = await requireAdmin(req);
    if (!gate.ok) return gate.res!;
    if (req.method === "POST" && req.headers.get("x-panel") !== "1") {
      return send({ ok: false, error: "missing panel header" }, 400);
    }
    if (limited(`api:${ip}`, 240, 60)) return send({ ok: false, error: "slow down" }, 429);

    const now = new Date();
    const todayKey = now.toLocaleDateString("en-CA", { timeZone: "Asia/Dhaka" });
    const dayOf = (s: string) => new Date(s).toLocaleDateString("en-CA", { timeZone: "Asia/Dhaka" });

    if (path === "api/me") {
      const row = rows(await svc.entities.NaiUser.filter({ telegram_user_id: SUPER_ADMIN }, { limit: 1 }))[0];
      return send({ ok: true, telegram_id: SUPER_ADMIN, role: "superadmin",
        display_name: row?.display_name || "Super Admin", username: row?.username || "" });
    }

    if (path === "api/stats") {
      const users = rows(await svc.entities.NaiUser.list("-created_date", 500));
      const installs = rows(await svc.entities.NaiInstallation.list("-created_date", 500));
      const acts = rows(await svc.entities.NaiActivation.list("-created_date", 500));
      const codes = rows(await svc.entities.AccessCode.list("-created_date", 500));
      const evts = rows(await svc.entities.NaiEvent.list("-created_date", 500));
      const actById = new Map(acts.map((a: any) => [a.activation_id, a]));
      let active = 0, pending = 0, expired = 0, revoked = 0;
      for (const i of installs) {
        const a = actById.get(i.activation_id);
        if (i.status === "revoked" || a?.status === "REVOKED") revoked += 1;
        else if (a?.status === "EXPIRED") expired += 1;
        else if (a?.status === "ACTIVE") active += 1;
        else pending += 1;
      }
      const byType: Record<string, number> = {};
      const daily: Record<string, number> = {};
      for (const e of evts) {
        byType[e.event_type] = (byType[e.event_type] || 0) + 1;
        if (e.event_type !== "code_issued") continue;
        const d = dayOf(e.created_date);
        daily[d] = (daily[d] || 0) + 1;
      }
      return send({
        ok: true,
        users: users.length, blocked: users.filter((u: any) => u.blocked === true).length,
        installations: installs.length, active, pending, expired, revoked,
        codes_today: codes.filter((c: any) => c.issued_at && dayOf(c.issued_at) === todayKey).length,
        events_today: evts.filter((e: any) => dayOf(e.created_date) === todayKey).length,
        events_by_type: byType,
        daily: Object.entries(daily).slice(-7).map(([d, n]) => ({ day: d, n })),
      });
    }

    if (path === "api/users") {
      const q = (url.searchParams.get("q") || "").trim();
      let users = rows(await svc.entities.NaiUser.list("-created_date", 500));
      if (q) users = users.filter((u: any) => String(u.telegram_user_id).includes(q) ||
        String(u.username || "").toLowerCase().includes(q.toLowerCase()) ||
        String(u.display_name || "").toLowerCase().includes(q.toLowerCase()));
      const installs = rows(await svc.entities.NaiInstallation.list("-created_date", 500));
      const out = users.slice(0, 200).map((u: any) => {
        const mine = installs.filter((i: any) => String(i.telegram_user_id) === String(u.telegram_user_id));
        return {
          telegram_user_id: u.telegram_user_id, username: u.username || "", display_name: u.display_name || "",
          blocked: u.blocked === true, registered: u.created_date,
          codes_today: Number(u.codes_today ?? 0), last_code_at: u.last_code_at || "",
          installations: mine.length, active: mine.filter((i: any) => i.status === "active").length,
        };
      });
      return send({ ok: true, users: out, total: out.length });
    }

    if (path.startsWith("api/users/")) {
      const tg = decodeURIComponent(path.slice("api/users/".length));
      const user = rows(await svc.entities.NaiUser.filter({ telegram_user_id: tg }, { limit: 1 }))[0];
      const installs = rows(await svc.entities.NaiInstallation.filter({ telegram_user_id: tg }, { limit: 50 }));
      const codes = rows(await svc.entities.AccessCode.filter({ tg_id: tg }, { limit: 50 }));
      const events = rows(await svc.entities.NaiEvent.filter({ telegram_user_id: tg }, { limit: 50 }));
      return send({ ok: true, user: user || null, installations: installs.map(installRow),
        codes: codes.map(codeRow), events: events.map(eventRow) });
    }

    if (path === "api/installations") {
      const q = (url.searchParams.get("q") || "").trim().toLowerCase();
      const state = url.searchParams.get("state") || "";
      const installs = rows(await svc.entities.NaiInstallation.list("-created_date", 500));
      const acts = rows(await svc.entities.NaiActivation.list("-created_date", 500));
      const actById = new Map(acts.map((a: any) => [a.activation_id, a]));
      let out = installs.map((i: any) => {
        const a = actById.get(i.activation_id);
        let s = "PENDING";
        if (i.status === "revoked" || a?.status === "REVOKED") s = "REVOKED";
        else if (a?.status === "EXPIRED") s = "EXPIRED";
        else if (a?.status === "ACTIVE") s = "ACTIVE";
        return { ...installRow(i), state: s, activation: a ? activationRow(a) : null };
      });
      if (state) out = out.filter((r: any) => r.state === state);
      if (q) out = out.filter((r: any) => r.tail.includes(q) || String(r.telegram_user_id).includes(q));
      return send({ ok: true, installations: out.slice(0, 300), total: out.length });
    }

    if (path === "api/codes") {
      const q = (url.searchParams.get("q") || "").trim().toLowerCase();
      const status = url.searchParams.get("status") || "";
      let codes = rows(await svc.entities.AccessCode.list("-created_date", 300));
      if (status) codes = codes.filter((c: any) => c.status === status);
      if (q) codes = codes.filter((c: any) => String(c.tg_id || "").includes(q) ||
        String(c.tg_name || "").toLowerCase().includes(q) ||
        String(c.id).toLowerCase().includes(q) || maskInstall(c.installation_id).includes(q));
      return send({ ok: true, codes: codes.slice(0, 300).map(codeRow), total: codes.length });
    }

    if (path === "api/events") {
      const type = url.searchParams.get("type") || "";
      const q = (url.searchParams.get("q") || "").trim();
      const from = url.searchParams.get("from") || "";
      const to = url.searchParams.get("to") || "";
      let evts = rows(await svc.entities.NaiEvent.list("-created_date", 400));
      if (type) evts = evts.filter((e: any) => e.event_type === type);
      if (q) evts = evts.filter((e: any) => String(e.telegram_user_id || "").includes(q) ||
        String(e.installation_id || "").includes(q) || String(e.detail || "").toLowerCase().includes(q.toLowerCase()));
      if (from) evts = evts.filter((e: any) => new Date(e.created_date) >= new Date(`${from}T00:00:00+06:00`));
      if (to) evts = evts.filter((e: any) => new Date(e.created_date) <= new Date(`${to}T23:59:59+06:00`));
      return send({ ok: true, events: evts.slice(0, 300).map(eventRow), total: evts.length });
    }

    if (path === "api/settings") {
      const out = [];
      for (const [key, meta] of Object.entries(SETTINGS)) {
        out.push({ key, ...meta, value: await setting(svc, key, meta.def) });
      }
      return send({ ok: true, settings: out });
    }

    if (path === "api/settings" && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const key = String(body.key ?? "");
      const check = validSetting(key, String(body.value ?? ""));
      if (!check.ok) return send({ ok: false, error: `${key}: ${check.why}` }, 400);
      await putSetting(svc, key, check.value!);
      await audit(svc, "admin_action", SUPER_ADMIN, "", ip, `setting ${key}=${check.value}`);
      return send({ ok: true, key, value: check.value });
    }

    if (path === "api/action" && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const action = String(body.action ?? "");
      const target = String(body.target ?? "").trim();
      if (body.confirm !== true) return send({ ok: false, error: "confirmation required" }, 400);
      if (limited(`act:${ip}`, 40, 60)) return send({ ok: false, error: "slow down" }, 429);

      if (action === "block" || action === "unblock") {
        const user = rows(await svc.entities.NaiUser.filter({ telegram_user_id: target }, { limit: 1 }))[0];
        if (!user) return send({ ok: false, error: "user not found" }, 404);
        await svc.entities.NaiUser.update(user.id, { blocked: action === "block" });
        await audit(svc, "blocked", target, "", ip, `${action} by super admin`);
        await audit(svc, "admin_action", SUPER_ADMIN, "", ip, `${action} user ${target}`);
        return send({ ok: true, message: `${target} ${action === "block" ? "blocked" : "unblocked"}` });
      }

      if (action === "revoke" || action === "reset") {
        const inst = rows(await svc.entities.NaiInstallation.filter({ installation_id: target }, { limit: 1 }))[0];
        if (!inst) return send({ ok: false, error: "installation not found" }, 404);
        const act = rows(await svc.entities.NaiActivation.filter({ activation_id: inst.activation_id }, { limit: 1 }))[0];
        if (act) await svc.entities.NaiActivation.update(act.id, { status: "REVOKED", revoked_at: new Date().toISOString() });
        if (action === "revoke") {
          await svc.entities.NaiInstallation.update(inst.id, { status: "revoked" });
        } else {
          await svc.entities.NaiInstallation.update(inst.id, { status: "reset", activation_id: "" });
        }
        await audit(svc, action === "revoke" ? "revoked" : "reset", String(inst.telegram_user_id || ""),
          target, ip, `${action} by super admin`);
        await audit(svc, "admin_action", SUPER_ADMIN, target, ip, `${action} installation …${target.slice(-6)}`);
        return send({ ok: true, message: `${action} applied` });
      }

      if (action === "revoke_code") {
        const rec = rows(await svc.entities.AccessCode.filter({ id: target }, { limit: 1 }))[0];
        if (!rec) return send({ ok: false, error: "code record not found" }, 404);
        if (rec.status === "bound") return send({ ok: false, error: "this code was already redeemed" }, 409);
        await svc.entities.AccessCode.update(rec.id, { status: "revoked" });
        await audit(svc, "revoked", String(rec.tg_id || ""), "", ip, "unused code revoked by super admin");
        await audit(svc, "admin_action", SUPER_ADMIN, "", ip, `revoke_code ${rec.id}`);
        return send({ ok: true, message: "code revoked" });
      }

      if (action === "reveal") {
        const inst = rows(await svc.entities.NaiInstallation.filter({ installation_id: target }, { limit: 1 }))[0];
        if (!inst) return send({ ok: false, error: "installation not found" }, 404);
        await audit(svc, "admin_action", SUPER_ADMIN, target, ip, "installation id revealed");
        return send({ ok: true, installation_id: target });
      }

      return send({ ok: false, error: "unknown action" }, 400);
    }

    return send({ ok: false, error: "not found" }, 404);
  }

  return send({ ok: false, error: "not found" }, 404);
});

// ── row mappers (never leak more than the panel needs) ─────────────────────
function installRow(i: any) {
  return {
    tail: String(i.installation_id || "").slice(-6),
    full: i.installation_id || "",
    masked: maskInstall(i.installation_id),
    telegram_user_id: i.telegram_user_id || "",
    status: i.status || "pending",
    created_at: i.created_date, last_seen: i.last_seen || "",
    ip: maskIp(i.last_known_ip || ""),
  };
}
function activationRow(a: any) {
  return {
    activation_id_tail: String(a.activation_id || "").slice(-6),
    status: a.status, activated_at: a.activated_at || "",
    expires_at: a.expires_at || "", last_verified: a.last_verified || "",
    ip: maskIp(a.last_ip || ""), revoked_at: a.revoked_at || "",
  };
}
function codeRow(c: any) {
  return {
    id: c.id, code_id: String(c.id).slice(-8),
    telegram_user_id: c.tg_id || "", name: c.tg_name || "",
    status: c.status || "issued", issued_at: c.issued_at || c.created_date,
    expires_at: c.expires_at || "", used_at: c.used_at || "",
    installation: c.installation_id ? maskInstall(c.installation_id) : "—",
    ip: maskIp(c.ip || ""),
    has_hash: Boolean(c.code_hash),
  };
}
function eventRow(e: any) {
  return {
    id: e.id, type: e.event_type, at: e.created_date,
    telegram_user_id: e.telegram_user_id || "",
    installation: e.installation_id ? maskInstall(e.installation_id) : "—",
    ip: maskIp(e.ip || ""), detail: e.detail || "",
  };
}

// ── HTML ───────────────────────────────────────────────────────────────────
function loginPage(message: string): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NAI Access · Super Admin</title>
<link rel="icon" href="data:,">
<style>
:root{--bg:#0b0f17;--card:#131a26;--line:#22304a;--txt:#e8eefc;--dim:#8fa3c4;--acc:#4f8cff;--ok:#28c76f;--warn:#ffab00;--bad:#ff5c5c}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(1200px 600px at 20% -10%,#16233a,#0b0f17 60%);color:var(--txt);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;display:grid;place-items:center;padding:24px}
.card{width:100%;max-width:420px;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:28px;box-shadow:0 30px 80px rgba(0,0,0,.45)}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;letter-spacing:.2px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--ok);box-shadow:0 0 12px var(--ok)}
h1{font-size:19px;margin:18px 0 4px}p{color:var(--dim);margin:0 0 18px;font-size:13px}
input{width:100%;padding:13px 14px;border-radius:12px;border:1px solid var(--line);background:#0e1520;color:var(--txt);font-size:16px;letter-spacing:3px;text-align:center}
button{margin-top:12px;width:100%;padding:12px;border:0;border-radius:12px;background:var(--acc);color:#fff;font-weight:600;font-size:14px;cursor:pointer}
button.ghost{background:#1a2434;border:1px solid var(--line)}
button:disabled{opacity:.5;cursor:default}
.msg{margin-top:14px;font-size:13px;color:var(--warn);min-height:18px}
.note{margin-top:18px;padding-top:14px;border-top:1px solid var(--line);color:var(--dim);font-size:12px}
</style></head><body>
<div class="card">
  <div class="brand"><span class="dot"></span> NAI ACCESS · SUPER ADMIN</div>
  <h1>Sign in</h1>
  <p>A one-time code is sent to the Super Admin's Telegram (${SUPER_ADMIN}).</p>
  <button id="send">Send me a login code</button>
  <div style="height:14px"></div>
  <input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="••••••">
  <button id="go" disabled>Verify &amp; open panel</button>
  <div class="msg" id="msg">${message ? message.replace(/</g, "&lt;") : ""}</div>
  <div class="note">Server-side session · HttpOnly · Signatures verified on every request</div>
</div>
<script>
const $=s=>document.querySelector(s);
let busy=false;
$('#send').onclick=async()=>{
  if(busy)return;busy=true;$('#send').disabled=true;$('#msg').textContent='Sending…';
  try{
    const B=location.search?location.pathname:location.pathname;const r=await fetch(B+'?r=auth/request',{method:'POST',credentials:'same-origin',headers:{'X-Panel':'1'}});
    const d=await r.json();
    $('#msg').textContent=d.ok?('Code sent to Telegram'+(d.delivered?'':' (delivery may have failed)')):(d.error||'could not send');
    if(d.ok){$('#go').disabled=false;$('#code').focus();}
  }catch(e){$('#msg').textContent='Network error';}
  busy=false;$('#send').disabled=false;
};
$('#go').onclick=async()=>{
  const code=$('#code').value.trim();
  if(!/^\\d{6}$/.test(code)){$('#msg').textContent='Enter the 6 digit code';return;}
  $('#msg').textContent='Verifying…';
  const r=await fetch(B+'?r=auth/verify',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Panel':'1'},body:JSON.stringify({code})});
  const d=await r.json();
  if(d.ok){location.href=B;}else{$('#msg').textContent=d.error||'wrong code';}
};
$('#code').addEventListener('input',e=>{$('#go').disabled=e.target.value.length!==6;});
</script></body></html>`;
}

function dashboardPage(_note = ""): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NAI Access · Control Panel</title>
<link rel="icon" href="data:,">
<style>
:root{--bg:#f6f8fc;--panel:#ffffff;--card:#ffffff;--line:#e3e9f2;--txt:#101828;--dim:#667085;--acc:#3b6ef6;--ok:#12b76a;--warn:#f79009;--bad:#f04438;--chip:#eef3ff;--side:#0d1526;--sidetxt:#c7d3ea}
[data-theme=dark]{--bg:#0b0f17;--panel:#111a28;--card:#131d2d;--line:#22304a;--txt:#e8eefc;--dim:#8fa3c4;--chip:#1c2942;--side:#080d16;--sidetxt:#a9b8d4}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.app{display:grid;grid-template-columns:248px 1fr;min-height:100vh}
.side{background:var(--side);color:var(--sidetxt);padding:22px 16px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:6px}
.brand{display:flex;align-items:center;gap:10px;color:#fff;font-weight:700;margin-bottom:18px;letter-spacing:.2px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--ok);box-shadow:0 0 12px var(--ok)}
.nav{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;cursor:pointer;font-size:13.5px;color:var(--sidetxt);border:1px solid transparent}
.nav:hover{background:rgba(255,255,255,.06)}
.nav.on{background:rgba(79,140,255,.16);border-color:rgba(79,140,255,.35);color:#fff}
.nav .ic{width:18px;text-align:center;opacity:.9}
.spacer{flex:1}
.foot{font-size:11px;color:#7f8fae;line-height:1.6}
.main{padding:22px 26px 60px;min-width:0}
.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.top h1{font-size:20px;margin:0;font-weight:700}
.top .sub{color:var(--dim);font-size:12.5px}
.right{margin-left:auto;display:flex;gap:8px;align-items:center}
.btn{background:var(--acc);color:#fff;border:0;border-radius:10px;padding:9px 14px;font-weight:600;font-size:13px;cursor:pointer}
.btn.ghost{background:transparent;color:var(--txt);border:1px solid var(--line)}
.btn.danger{background:var(--bad)}
.btn.warn{background:var(--warn);color:#231a00}
.btn.sm{padding:6px 10px;font-size:12px;border-radius:8px}
.grid{display:grid;gap:14px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.kpi .l{color:var(--dim);font-size:12px;letter-spacing:.3px;text-transform:uppercase}
.kpi .v{font-size:26px;font-weight:700;margin-top:6px}
.kpi .h{font-size:11.5px;color:var(--dim);margin-top:4px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:16px}
.panel h2{font-size:14.5px;margin:0 0 12px;display:flex;align-items:center;gap:8px}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
input.search,select,input.txt{background:var(--bg);border:1px solid var(--line);color:var(--txt);border-radius:10px;padding:9px 12px;font-size:13px;min-width:150px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--dim);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.4px;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px;border-bottom:1px solid var(--line);vertical-align:middle}
tr:hover td{background:rgba(79,140,255,.06)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.badge{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:999px;font-size:11.5px;font-weight:600;background:var(--chip);color:var(--txt)}
.badge.ok{background:rgba(18,183,106,.14);color:var(--ok)}
.badge.warn{background:rgba(247,144,9,.16);color:var(--warn)}
.badge.bad{background:rgba(240,68,56,.14);color:var(--bad)}
.badge.dim{background:var(--chip);color:var(--dim)}
.empty{color:var(--dim);padding:22px;text-align:center;font-size:13px}
.bars{display:flex;align-items:flex-end;gap:8px;height:90px;margin-top:6px}
.bars .b{flex:1;background:linear-gradient(180deg,var(--acc),rgba(79,140,255,.35));border-radius:6px 6px 3px 3px;min-height:3px;position:relative}
.bars .b span{position:absolute;bottom:-18px;left:0;right:0;text-align:center;font-size:10px;color:var(--dim)}
.modal{position:fixed;inset:0;background:rgba(4,8,15,.6);display:none;place-items:center;padding:20px;z-index:50}
.modal.on{display:grid}
.modal .box{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px;max-width:440px;width:100%;box-shadow:0 40px 90px rgba(0,0,0,.5)}
.modal h3{margin:0 0 8px;font-size:16px}
.modal p{color:var(--dim);font-size:13px;margin:0 0 16px}
.row{display:flex;gap:8px;justify-content:flex-end}
.toast{position:fixed;right:18px;bottom:18px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;font-size:13px;box-shadow:0 20px 50px rgba(0,0,0,.35);opacity:0;transform:translateY(10px);transition:.25s;z-index:60;max-width:340px}
.toast.on{opacity:1;transform:none}
.setting{display:grid;grid-template-columns:1fr 220px;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid var(--line)}
.setting .t{font-weight:600;font-size:13.5px}
.setting .h{color:var(--dim);font-size:12px}
.grid2{grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.detail{display:grid;grid-template-columns:160px 1fr;gap:6px 14px;font-size:13px}
.detail .k{color:var(--dim)}
.mobile-only{display:none}
@media(max-width:900px){
 .app{grid-template-columns:1fr}
 .side{position:fixed;left:0;right:0;bottom:0;top:auto;height:auto;flex-direction:row;overflow-x:auto;padding:8px;z-index:40;border-top:1px solid var(--line)}
 .brand,.foot{display:none}
 .nav{flex:0 0 auto;font-size:12px;padding:8px 10px}
 .nav .ic{display:none}
 .main{padding:18px 14px 96px}
 .setting{grid-template-columns:1fr}
 .kpi .v{font-size:22px}
}
</style></head><body>
<div class="app">
  <aside class="side">
    <div class="brand"><span class="dot"></span> NAI ACCESS · ADMIN</div>
    <div class="nav on" data-s="overview"><span class="ic">◧</span> Overview</div>
    <div class="nav" data-s="users"><span class="ic">◍</span> Users</div>
    <div class="nav" data-s="installs"><span class="ic">▣</span> Installations</div>
    <div class="nav" data-s="codes"><span class="ic">⚿</span> Access Codes</div>
    <div class="nav" data-s="events"><span class="ic">⚡</span> Security Events</div>
    <div class="nav" data-s="settings"><span class="ic">⚙</span> System Settings</div>
    <div class="nav" data-s="profile"><span class="ic">☺</span> Admin Profile</div>
    <div class="spacer"></div>
    <div class="foot">NAI Access System<br>Developed by Selim SB</div>
  </aside>
  <main class="main">
    <div class="top">
      <div>
        <h1 id="title">Overview</h1>
        <div class="sub" id="subtitle">Live state of the access system</div>
      </div>
      <div class="right">
        <button class="btn ghost sm" id="theme">Dark</button>
        <button class="btn ghost sm" id="reload">Refresh</button>
        <button class="btn sm" id="logout">Sign out</button>
      </div>
    </div>
    <div id="view"></div>
  </main>
</div>
<div class="modal" id="modal"><div class="box">
  <h3 id="mtitle">Confirm</h3><p id="mtext"></p>
  <div class="row"><button class="btn ghost" id="mcancel">Cancel</button><button class="btn danger" id="mok">Confirm</button></div>
</div></div>
<div class="toast" id="toast"></div>
<script>
const BASE=location.pathname;
const $=s=>document.querySelector(s), api=(p,o={})=>fetch(BASE+'?r=api/'+String(p).replace(/^\//,'').replace(/\?/g,'&'),{credentials:'same-origin',...o,headers:{'X-Panel':'1','Content-Type':'application/json',...(o.headers||{})}}).then(r=>r.json());
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dt=s=>{if(!s)return '—';const d=new Date(s);return isNaN(d)?'—':d.toLocaleString('en-GB',{timeZone:'Asia/Dhaka',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:true});};
let toastT;const toast=m=>{const t=$('#toast');t.textContent=m;t.classList.add('on');clearTimeout(toastT);toastT=setTimeout(()=>t.classList.remove('on'),3200);};
let confirmCb=null;
const ask=(title,text,cb)=>{$('#mtitle').textContent=title;$('#mtext').textContent=text;confirmCb=cb;$('#modal').classList.add('on');};
$('#mcancel').onclick=()=>{$('#modal').classList.remove('on');confirmCb=null;};
$('#mok').onclick=async()=>{$('#modal').classList.remove('on');if(confirmCb)await confirmCb();confirmCb=null;};
const badge=s=>{const m={ACTIVE:'ok',PENDING:'warn',EXPIRED:'warn',REVOKED:'bad',issued:'warn',bound:'ok',revoked:'bad',expired:'dim'};return '<span class="badge '+(m[s]||'dim')+'">'+esc(s)+'</span>';};
const state={section:'overview',user:null};
const titles={overview:['Overview','Live state of the access system'],users:['Users','Telegram accounts, their devices and code history'],installs:['Installations','Every device identity the server knows'],codes:['Access Codes','Issued codes, their state and redemption'],events:['Security Events','Audit trail written by the server'],settings:['System Settings','Validated server side before saving'],profile:['Admin Profile','The only identity allowed in here']};

function setSection(s){state.section=s;document.querySelectorAll('.nav').forEach(n=>n.classList.toggle('on',n.dataset.s===s));
 const t=titles[s]||['',''];$('#title').textContent=t[0];$('#subtitle').textContent=t[1];render();}
document.querySelectorAll('.nav').forEach(n=>n.onclick=()=>setSection(n.dataset.s));

async function render(){
 const v=$('#view');
 if(state.section==='overview'){v.innerHTML='<div class="empty">Loading…</div>';const d=await api('stats');if(!d.ok)return v.innerHTML='<div class="empty">'+esc(d.error)+'</div>';
  const K=(l,n,h,cls)=>'<div class="kpi"><div class="l">'+l+'</div><div class="v '+(cls||'')+'">'+n+'</div><div class="h">'+(h||'')+'</div></div>';
  const bars=(d.daily||[]).map(x=>'<div class="b" style="height:'+Math.max(6,Math.min(100,x.n*12))+'%" title="'+x.day+': '+x.n+'"><span>'+x.day.slice(5)+'</span></div>').join('')||'<div class="empty">No codes yet</div>';
  const types=Object.entries(d.events_by_type||{}).sort((a,b)=>b[1]-a[1]).map(([k,n])=>'<span class="badge dim" style="margin:3px 4px 0 0">'+esc(k)+' · '+n+'</span>').join('');
  v.innerHTML='<div class="grid kpis">'+K('Total Telegram users',d.users,d.blocked+' blocked')+K('Active installations',d.active,'of '+d.installations+' total')+K('Pending activations',d.pending,'awaiting a code')+K('Expired activations',d.expired,'past their validity')+K('Revoked installations',d.revoked,'access withdrawn')+K('Blocked users',d.blocked,'cannot request codes')+K('Codes generated today',d.codes_today,'Asia/Dhaka day')+K('Security events today',d.events_today,'all types')+'</div>'+
   '<div class="panel"><h2>Codes generated · last 7 active days</h2><div class="bars">'+bars+'</div><div style="height:26px"></div></div>'+
   '<div class="panel"><h2>Event mix</h2>'+ (types||'<div class="empty">No events recorded yet</div>') +'</div>';
 }
 if(state.section==='users'){v.innerHTML='<div class="panel"><div class="toolbar"><input class="search" id="q" placeholder="Search Telegram ID, username, name"><button class="btn ghost sm" id="go">Search</button></div><div id="tbl"><div class="empty">Loading…</div></div></div>';
  const load=async()=>{const q=$('#q').value.trim();const d=await api('users?q='+encodeURIComponent(q));
   $('#tbl').innerHTML=d.users.length?'<table><thead><tr><th>Telegram ID</th><th>Name</th><th>Registered</th><th>Devices</th><th>Codes today</th><th>Status</th><th></th></tr></thead><tbody>'+
    d.users.map(u=>'<tr><td class="mono">'+esc(u.telegram_user_id)+'</td><td>'+esc(u.display_name||u.username||'—')+'</td><td>'+dt(u.registered)+'</td><td>'+u.active+' / '+u.installations+'</td><td>'+u.codes_today+'</td><td>'+(u.blocked?'<span class="badge bad">blocked</span>':'<span class="badge ok">active</span>')+'</td>'+
    '<td style="text-align:right"><button class="btn ghost sm" data-u="'+esc(u.telegram_user_id)+'">Open</button></td></tr>').join('')+'</tbody></table>':'<div class="empty">No users match</div>';
   $('#tbl').querySelectorAll('[data-u]').forEach(b=>b.onclick=()=>openUser(b.dataset.u));};
  $('#go').onclick=load;$('#q').onkeydown=e=>{if(e.key==='Enter')load();};load();
 }
 if(state.section==='installs'){v.innerHTML='<div class="panel"><div class="toolbar"><input class="search" id="q" placeholder="Search installation or Telegram ID"><select id="st"><option value="">All states</option><option>ACTIVE</option><option>PENDING</option><option>EXPIRED</option><option>REVOKED</option></select><button class="btn ghost sm" id="go">Filter</button></div><div id="tbl"><div class="empty">Loading…</div></div></div>';
  const load=async()=>{const d=await api('installations?q='+encodeURIComponent($('#q').value.trim())+'&state='+$('#st').value);
   $('#tbl').innerHTML=d.installations.length?'<table><thead><tr><th>Installation</th><th>Telegram ID</th><th>State</th><th>Activated</th><th>Last verified</th><th>Last IP</th><th></th></tr></thead><tbody>'+
    d.installations.map(i=>'<tr><td class="mono" id="id_'+esc(i.tail)+'">'+esc(i.masked)+' <button class="btn ghost sm" data-show="'+esc(i.full)+'">Reveal</button></td><td class="mono">'+esc(i.telegram_user_id)+'</td><td>'+badge(i.state)+'</td><td>'+dt(i.activation?.activated_at)+'</td><td>'+dt(i.last_seen)+'</td><td class="mono">'+esc(i.ip)+'</td>'+
    '<td style="text-align:right;white-space:nowrap"><button class="btn warn sm" data-r="'+esc(i.full)+'">Reset</button> <button class="btn danger sm" data-v="'+esc(i.full)+'" data-t="'+esc(i.tail)+'">Revoke</button></td></tr>').join('')+'</tbody></table>':'<div class="empty">No installations match</div>';
   $('#tbl').querySelectorAll('[data-show]').forEach(b=>b.onclick=async()=>{
     const r=await api('action',{method:'POST',body:JSON.stringify({action:'reveal',target:b.dataset.show,confirm:true})});
     if(r.ok&&r.installation_id){b.parentElement.textContent=r.installation_id;toast('Revealed · logged in the audit trail');}});
   $('#tbl').querySelectorAll('[data-v]').forEach(b=>b.onclick=()=>ask('Revoke installation','Access for ••••••'+b.dataset.t+' is withdrawn immediately. The device is refused at its next server check. Continue?',async()=>{
     const r=await api('action',{method:'POST',body:JSON.stringify({action:'revoke',target:b.dataset.v,confirm:true})});
     toast(r.ok?'Revoked':(r.error||'Failed'));render();}));
   $('#tbl').querySelectorAll('[data-r]').forEach(b=>b.onclick=()=>ask('Reset installation','The activation ending ••••••'+b.dataset.r.slice(-6)+' is closed and the device must activate again with a new code. Continue?',async()=>{
     const r=await api('action',{method:'POST',body:JSON.stringify({action:'reset',target:b.dataset.r,confirm:true})});
     toast(r.ok?'Reset applied':(r.error||'Failed'));render();}));
  $('#go').onclick=load;$('#q').onkeydown=e=>{if(e.key==='Enter')load();};$('#st').onchange=load;load();
 }
 if(state.section==='codes'){v.innerHTML='<div class="panel"><div class="toolbar"><input class="search" id="q" placeholder="Search Code ID, user or installation"><select id="st"><option value="">All statuses</option><option>issued</option><option>bound</option><option>expired</option><option>revoked</option></select><button class="btn ghost sm" id="go">Filter</button></div><div id="tbl"><div class="empty">Loading…</div></div><div class="empty" style="text-align:left;padding:14px 0 0">Codes are stored only as SHA-256 hashes. No stored hash is ever shown as a usable code.</div></div>';
  const load=async()=>{const d=await api('codes?q='+encodeURIComponent($('#q').value.trim())+'&status='+$('#st').value);
   $('#tbl').innerHTML=d.codes.length?'<table><thead><tr><th>Code ID</th><th>User</th><th>Status</th><th>Issued</th><th>Expires</th><th>Redeemed</th><th>Installation</th><th></th></tr></thead><tbody>'+
    d.codes.map(c=>'<tr><td class="mono">'+esc(c.code_id)+'</td><td>'+esc(c.name||c.telegram_user_id)+'</td><td>'+badge(c.status)+'</td><td>'+dt(c.issued_at)+'</td><td>'+dt(c.expires_at)+'</td><td>'+dt(c.used_at)+'</td><td class="mono">'+esc(c.installation)+'</td>'+
    '<td style="text-align:right">'+(c.status==='issued'?'<button class="btn danger sm" data-x="'+esc(c.id)+'">Revoke</button>':'<span class="badge dim">locked</span>')+'</td></tr>').join('')+'</tbody></table>':'<div class="empty">No codes match</div>';
   $('#tbl').querySelectorAll('[data-x]').forEach(b=>b.onclick=()=>ask('Revoke unused code','This code can no longer activate a device. Continue?',async()=>{
     const r=await api('action',{method:'POST',body:JSON.stringify({action:'revoke_code',target:b.dataset.x,confirm:true})});toast(r.ok?'Code revoked':(r.error||'Failed'));render();}));};
  $('#go').onclick=load;$('#q').onkeydown=e=>{if(e.key==='Enter')load();};$('#st').onchange=load;load();
 }
 if(state.section==='events'){const types=['','code_issued','activation','ip_change','reuse_attempt','expired_code','revoked','reset','blocked','admin_action','panel_login','panel_auth_fail'];
  v.innerHTML='<div class="panel"><div class="toolbar"><select id="ty">'+types.map(t=>'<option value="'+t+'">'+(t||'All events')+'</option>').join('')+'</select><input class="search" id="q" placeholder="User ID, installation or text"><input class="txt" type="date" id="from"><input class="txt" type="date" id="to"><button class="btn ghost sm" id="go">Apply</button></div><div id="tbl"><div class="empty">Loading…</div></div></div>';
  const load=async()=>{const d=await api('events?type='+encodeURIComponent($('#ty').value)+'&q='+encodeURIComponent($('#q').value.trim())+'&from='+$('#from').value+'&to='+$('#to').value);
   $('#tbl').innerHTML=d.events.length?'<table><thead><tr><th>When</th><th>Event</th><th>User</th><th>Installation</th><th>IP</th><th>Detail</th></tr></thead><tbody>'+
    d.events.map(e=>'<tr><td>'+dt(e.at)+'</td><td>'+badge(e.type)+'</td><td class="mono">'+esc(e.telegram_user_id||'—')+'</td><td class="mono">'+esc(e.installation)+'</td><td class="mono">'+esc(e.ip)+'</td><td>'+esc(e.detail||'—')+'</td></tr>').join('')+'</tbody></table>':'<div class="empty">No events match</div>';};
  $('#go').onclick=load;$('#q').onkeydown=e=>{if(e.key==='Enter')load();};$('#ty').onchange=load;load();
 }
 if(state.section==='settings'){v.innerHTML='<div class="panel"><div class="empty">Loading…</div></div>';const d=await api('settings');
  v.innerHTML='<div class="panel"><h2>Access policy</h2>'+d.settings.map(s=>'<div class="setting"><div><div class="t">'+esc(s.label)+'</div><div class="h">'+esc(s.help)+' · allowed '+s.min+'–'+s.max+'</div></div>'+
   '<div style="display:flex;gap:8px"><input class="txt" style="width:90px" id="s_'+s.key+'" value="'+esc(s.value)+'"><button class="btn sm" data-k="'+s.key+'">Save</button></div></div>').join('')+
   '<div class="empty" style="text-align:left;padding-top:14px">Values are re-validated on the server; an out of range number is refused.</div></div>';
  v.querySelectorAll('[data-k]').forEach(b=>b.onclick=async()=>{const k=b.dataset.k,val=$('#s_'+k).value;
   const r=await api('settings',{method:'POST',body:JSON.stringify({key:k,value:val})});toast(r.ok?('Saved '+k):(r.error||'Refused'));});}
 if(state.section==='profile'){const me=await api('me');
  v.innerHTML='<div class="panel"><h2>Super Admin</h2><div class="detail"><div class="k">Telegram ID</div><div class="mono">'+esc(me.telegram_id)+'</div>'+
   '<div class="k">Role</div><div>'+badge('ACTIVE')+' superadmin</div><div class="k">Display name</div><div>'+esc(me.display_name)+'</div>'+
   '<div class="k">Session</div><div>Signed cookie, HttpOnly, SameSite=Strict, 8 hours</div>'+
   '<div class="k">Sign-in method</div><div>One-time code delivered by @NAI_AccessBot</div></div>'+
   '<div class="empty" style="text-align:left;padding-top:16px">Only one Telegram id is accepted by the server. Everyone else is rejected before any data is read.</div></div>';
 }
}

async function openUser(tg){
 const d=await api('users/'+encodeURIComponent(tg));
 if(!d.ok)return toast(d.error||'Failed');
 const u=d.user||{};
 const v=$('#view');
 v.innerHTML='<div class="panel"><h2>User <span class="mono">'+esc(tg)+'</span> '+(u.blocked?'<span class="badge bad">blocked</span>':'<span class="badge ok">active</span>')+'</h2>'+
  '<div class="detail"><div class="k">Name</div><div>'+esc(u.display_name||'—')+'</div><div class="k">Username</div><div>'+esc(u.username||'—')+'</div>'+
  '<div class="k">Registered</div><div>'+dt(u.created_date)+'</div><div class="k">Codes today</div><div>'+esc(u.codes_today??0)+'</div></div>'+
  '<div style="margin-top:14px"><button class="btn '+(u.blocked?'':'danger')+'" id="blk">'+(u.blocked?'Unblock user':'Block user')+'</button> <button class="btn ghost" id="back">Back to users</button></div></div>'+
  '<div class="panel"><h2>Installations</h2>'+(d.installations.length?'<table><thead><tr><th>Installation</th><th>State</th><th>Activated</th><th>Last verified</th><th>IP</th></tr></thead><tbody>'+
   d.installations.map(i=>'<tr><td class="mono">'+esc(i.masked)+'</td><td>'+badge(i.status)+'</td><td>'+dt(i.created_at)+'</td><td>'+dt(i.last_seen)+'</td><td class="mono">'+esc(i.ip)+'</td></tr>').join('')+'</tbody></table>':'<div class="empty">No devices yet</div>')+'</div>'+
  '<div class="panel"><h2>Code history</h2>'+(d.codes.length?'<table><thead><tr><th>Code ID</th><th>Status</th><th>Issued</th><th>Expires</th><th>Redeemed</th></tr></thead><tbody>'+
   d.codes.map(c=>'<tr><td class="mono">'+esc(c.code_id)+'</td><td>'+badge(c.status)+'</td><td>'+dt(c.issued_at)+'</td><td>'+dt(c.expires_at)+'</td><td>'+dt(c.used_at)+'</td></tr>').join('')+'</tbody></table>':'<div class="empty">No codes issued</div>')+'</div>'+
  '<div class="panel"><h2>Security events</h2>'+(d.events.length?'<table><thead><tr><th>When</th><th>Event</th><th>Detail</th></tr></thead><tbody>'+
   d.events.map(e=>'<tr><td>'+dt(e.at)+'</td><td>'+badge(e.type)+'</td><td>'+esc(e.detail||'—')+'</td></tr>').join('')+'</tbody></table>':'<div class="empty">No events</div>')+'</div>';
 $('#back').onclick=()=>setSection('users');
 $('#blk').onclick=()=>ask(u.blocked?'Unblock user':'Block user',u.blocked?'This user will be able to request codes again.':'This user will no longer be able to request access codes. Their existing devices stay active until revoked.',async()=>{
   const r=await api('action',{method:'POST',body:JSON.stringify({action:u.blocked?'unblock':'block',target:tg,confirm:true})});
   toast(r.ok?'Updated':(r.error||'Failed'));openUser(tg);});
}

$('#reload').onclick=()=>render();
$('#logout').onclick=async()=>{await fetch(BASE+'?r=auth/logout',{method:'POST',credentials:'same-origin',headers:{'X-Panel':'1'}});location.reload();};
const root=document.documentElement;
$('#theme').onclick=()=>{const dark=root.getAttribute('data-theme')==='dark';root.setAttribute('data-theme',dark?'light':'dark');$('#theme').textContent=dark?'Dark':'Light';localStorage.setItem('nai_theme',dark?'light':'dark');};
if(localStorage.getItem('nai_theme')==='dark'){root.setAttribute('data-theme','dark');$('#theme').textContent='Light';}
setSection('overview');
</script></body></html>`;
}
