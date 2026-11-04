// ============================================================
// NAI ACCESS BOT v2 — @NAI_AccessBot
//
// /start            menu only, never generates a code by itself
// 🎟 Generate       asks naiAccess for a code (channel membership required)
// 📊 My Status      that person's own activation picture
// ❓ Help           how it works
//
// Owner only:
//   /admin  /find <tgid>  /revoke <installation|code>  /reset <installation>
//   /block <tgid>  /unblock <tgid>  /config <key> <value>
//
// The bot never decides access. It asks naiAccess and repeats the answer.
// ============================================================
import { createClientFromRequest } from "npm:@base44/sdk@0.8.54";

const BOT_TOKEN = Deno.env.get("TELEGRAM_BOT_TOKEN_6") ?? "";
// Environment-only, same variable naiAccess reads. If it is missing every
// webhook call is refused (fail closed) instead of accepting a known value.
const WEBHOOK_SECRET = Deno.env.get("NAI_INTERNAL_SECRET") ?? "";
const API_URL = "https://solas-5609a435.base44.app/functions/naiAccess";
const OWNER_ID = "8012564523";
const CHANNEL_ID = "-1004394321090";
const CHANNEL_URL = "https://t.me/FF_FreeLike_BD";
const TG = `https://api.telegram.org/bot${BOT_TOKEN}`;

async function tg(method: string, body: Record<string, unknown>) {
  const res = await fetch(`${TG}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return await res.json();
}

async function api(payload: Record<string, unknown>) {
  try {
    const res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-nai-internal": WEBHOOK_SECRET },
      body: JSON.stringify(payload),
    });
    return await res.json();
  } catch (_e) {
    return { ok: false, status: "OFFLINE", message: "access server unreachable" };
  }
}

const esc = (v: string) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const short = (v: string) => `…${String(v).slice(-6)}`;

function when(iso: string): string {
  if (!iso) return "—";
  const t = new Date(iso);
  if (isNaN(t.getTime())) return "—";
  const p = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Dhaka", day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: true,
  }).formatToParts(t).reduce((a: any, x) => (a[x.type] = x.value, a), {});
  return `${p.day} ${p.month} ${p.year}, ${p.hour}:${p.minute} ${String(p.dayPeriod ?? "").toUpperCase()}`;
}

const MENU = {
  inline_keyboard: [
    [{ text: "🎟 Generate Access Code", callback_data: "gen" }],
    [{ text: "📊 My Access Status", callback_data: "status" }, { text: "❓ Help & Support", callback_data: "help" }],
  ],
};

async function isMember(userId: string): Promise<{ member: boolean; error?: string }> {
  const r = await tg("getChatMember", { chat_id: CHANNEL_ID, user_id: Number(userId) });
  if (!r?.ok) {
    const d = String(r?.description ?? "");
    if (d.includes("PARTICIPANT_ID_INVALID") || d.includes("user not found")) return { member: false };
    return { member: false, error: d || "membership check failed" };
  }
  const st = String(r.result?.status ?? "");
  return {
    member: ["creator", "administrator", "member"].includes(st) ||
      (st === "restricted" && r.result?.is_member === true),
  };
}

function codeCard(name: string, code: string, ttl: number): string {
  return [
    "✅ <b>ACCESS VERIFIED SUCCESSFULLY</b>",
    "",
    `𖣘 亗 ${esc(name || "friend")} 亗 𖣘`,
    "",
    "🎉 অভিনন্দন! তোমার Access Code সফলভাবে জেনারেট হয়েছে।",
    "",
    "🔐 <b>YOUR ACCESS CODE</b>",
    "",
    `"<code>${code}</code>"`,
    "",
    "━━━━━━━━━━━━━━━━━━",
    "",
    "📲 <b>HOW TO ACTIVATE</b>",
    "",
    "1. Termux ওপেন করো।",
    "2. যেখানে Access Code চাওয়া হচ্ছে, সেখানে কোডটি Paste করো।",
    "3. Activation সম্পন্ন হওয়া পর্যন্ত অপেক্ষা করো।",
    "",
    "⚠️ <b>IMPORTANT SECURITY NOTICE</b>",
    "",
    `⏳ <b>Validity:</b> মাত্র ${ttl} মিনিট।`,
    "",
    "📱 <b>Device Lock:</b> প্রথম যে ইনস্টলেশনে কোডটি ব্যবহার করা হবে, সেটির সাথেই লক হয়ে যাবে।",
    "",
    "🌐 <b>Network Security:</b> অন্য ফোন বা অননুমোদিত নেটওয়ার্কে এই কোড কাজ করবে না।",
    "",
    "🔒 <b>Security:</b> তোমার Access Code অন্য কারো সাথে শেয়ার করো না।",
    "",
    "━━━━━━━━━━━━━━━━━━",
    "",
    "💠 <i>Powered by NAI Access System</i>",
    "",
    "Developed by <b>Selim SB</b>",
  ].join("\n");
}

const STATE_ICON: Record<string, string> = {
  ACTIVE: "🟢", PENDING: "🕓", EXPIRED: "⌛", REVOKED: "🚫", NOT_ACTIVATED: "⚪",
};

function statusCard(s: any): string {
  const st = String(s.status ?? "NOT_ACTIVATED");
  const list = Array.isArray(s.installations) ? s.installations : [];
  const lines = [
    "📊 <b>MY ACCESS STATUS</b>",
    "",
    `👤 Telegram ID: <code>${esc(String(s.telegram_user_id ?? ""))}</code>`,
    `${STATE_ICON[st] ?? "⚪"} Status: <b>${st}</b>`,
  ];
  if (s.blocked) lines.push("🚫 This account is blocked.");
  if (!list.length) {
    lines.push("", "<i>No installation is registered to this Telegram account yet.</i>",
      "Tap 🎟 Generate Access Code to get one.");
  }
  list.forEach((i: any, n: number) => {
    lines.push("", `━━ <b>Installation ${n + 1}</b> ${STATE_ICON[i.state] ?? "⚪"} ${i.state}`,
      `🔑 <code>${esc(i.installation)}</code>`,
      `📅 Activated: ${when(i.activated_at)}`,
      i.last_verified ? `👁 Last verified: ${when(i.last_verified)}` : "👁 Never verified",
      `🌐 IP binding: ${i.bound_ip ? "recorded" : "—"}`);
  });
  lines.push("", "💠 <i>Powered by NAI Access System</i>", "", "Developed by <b>Selim SB</b>");
  return lines.join("\n");
}

async function send(chatId: number, text: string, extra: Record<string, unknown> = {}) {
  const r = await tg("sendMessage", {
    chat_id: chatId, text, parse_mode: "HTML",
    link_preview_options: { is_disabled: true }, ...extra,
  });
  if (!r?.ok) {
    await tg("sendMessage", { chat_id: chatId, text: text.replace(/<[^>]+>/g, ""), ...extra });
  }
  return r;
}

Deno.serve(async (req) => {
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ ok: true, service: "nai-access-bot", version: 2 }), {
      headers: { "Content-Type": "application/json" },
    });
  }
  if (!WEBHOOK_SECRET || req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== WEBHOOK_SECRET) {
    return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  }

  let update: any = {};
  try { update = await req.json(); } catch { return new Response(JSON.stringify({ ok: true })); }
  const dbg = update.debug === true;
  const svc = createClientFromRequest(req).asServiceRole;

  // ---------- buttons ----------
  if (update.callback_query) {
    const cb = update.callback_query;
    const uid = String(cb.from?.id ?? "");
    const name = [cb.from?.first_name, cb.from?.last_name].filter(Boolean).join(" ") || "friend";
    const chatId = cb.message?.chat?.id;
    const data = String(cb.data ?? "");

    if (data === "gen") {
      const { member, error } = await isMember(uid);
      if (dbg) return new Response(JSON.stringify({ ok: true, member, error: error ?? null }));
      if (!member) {
        await tg("answerCallbackQuery", { callback_query_id: cb.id, text: "চ্যানেলে জয়েন করো, তারপর আবার চাপো", show_alert: true });
        await send(chatId, [
          `👋 <b>${esc(name)}</b>, কোড পেতে হলে আগে আমাদের চ্যানেলে জয়েন করতে হবে।`,
          "",
          "1️⃣ নিচের <b>চ্যানেলে জয়েন করো</b> বাটনে চাপো",
          "2️⃣ চ্যানেলে ঢুকে <b>Join</b> চাপো",
          "3️⃣ ফিরে এসে <b>🎟 Generate Access Code</b> আবার চাপো",
        ].join("\n"), {
          reply_markup: { inline_keyboard: [[{ text: "📢 চ্যানেলে জয়েন করো", url: CHANNEL_URL }], [{ text: "🎟 আবার চেষ্টা করো", callback_data: "gen" }]] },
        });
        return new Response(JSON.stringify({ ok: true }));
      }

      const out = await api({ action: "generate", tg_user_id: uid, username: cb.from?.username ?? "", display_name: name });
      if (dbg) return new Response(JSON.stringify({ ok: true, api: out }));
      await tg("answerCallbackQuery", { callback_query_id: cb.id, text: out.ok ? "✅ কোড তৈরি হয়েছে" : "⚠️ সম্ভব হয়নি" });

      if (out.ok) {
        await send(chatId, codeCard(name, out.code, out.ttl_minutes ?? 5));
      } else if (out.status === "ALREADY_ACTIVE") {
        await send(chatId, [
          "🟢 <b>ALREADY ACTIVE</b>",
          "",
          "তোমার ইনস্টলেশন আগেই activate করা আছে। নতুন কোড লাগবে না।",
          "",
          "নতুন ফোনে বা নতুন ইনস্টলেশনে চালাতে চাইলে সেটার Access Code দরকার হবে।",
          "",
          "Developed by <b>Selim SB</b>",
        ].join("\n"), { reply_markup: MENU });
      } else if (out.status === "TOO_SOON") {
        await send(chatId, `⏳ একটু অপেক্ষা করো — ${out.wait_seconds ?? 15} সেকেন্ড পর আবার চাপো।`);
      } else if (out.status === "BLOCKED") {
        await send(chatId, "🚫 এই অ্যাকাউন্ট থেকে কোড নেওয়া বন্ধ করা হয়েছে।");
      } else {
        await send(chatId, `⚠️ কোড তৈরি করা যায়নি: ${out.message ?? out.status ?? "unknown"}`);
      }
      return new Response(JSON.stringify({ ok: true }));
    }

    if (data === "status") {
      const out = await api({ action: "status", tg_user_id: uid });
      if (dbg) return new Response(JSON.stringify({ ok: true, api: out }));
      await tg("answerCallbackQuery", { callback_query_id: cb.id });
      await send(chatId, statusCard({ ...out, telegram_user_id: uid }), { reply_markup: MENU });
      return new Response(JSON.stringify({ ok: true }));
    }

    if (data === "help") {
      if (dbg) return new Response(JSON.stringify({ ok: true, help: true }));
      await tg("answerCallbackQuery", { callback_query_id: cb.id });
      await send(chatId, [
        "❓ <b>HELP & SUPPORT</b>",
        "",
        "🎟 <b>Generate Access Code</b> — এক কোড, এক ইনস্টলেশন। ৫ মিনিটে মেয়াদ শেষ।",
        "📊 <b>My Access Status</b> — তোমার ইনস্টলেশন ও অ্যাক্টিভেশন অবস্থা।",
        "",
        "📱 নতুন ফোন বা নতুন ইনস্টলেশন = নতুন কোড।",
        "♻️ একই ইনস্টলেশন বারবার চালানো যাবে, নতুন কোড লাগবে না।",
        "🚫 মেয়াদোত্তীর্ণ বা বাতিল কোড কাজ করবে না।",
        "",
        "কোনো সমস্যা হলে owner-কে জানাও।",
        "",
        "Developed by <b>Selim SB</b>",
      ].join("\n"), { reply_markup: MENU });
      return new Response(JSON.stringify({ ok: true }));
    }

    return new Response(JSON.stringify({ ok: true }));
  }

  // ---------- text commands ----------
  const msg = update.message ?? update.edited_message;
  if (!msg?.chat?.id) return new Response(JSON.stringify({ ok: true }));
  const chatId = msg.chat.id;
  const uid = String(msg.from?.id ?? chatId);
  const name = [msg.from?.first_name, msg.from?.last_name].filter(Boolean).join(" ") || "friend";
  const text = String(msg.text ?? "").trim();
  const [cmd, ...args] = text.split(/\s+/);

  if (cmd === "/start" || text === "") {
    if (dbg) return new Response(JSON.stringify({ ok: true, menu: true }));
    await send(chatId, [
      `👋 স্বাগতম, <b>${esc(name)}</b>!`,
      "",
      "এখান থেকে তোমার Access Code নিতে পারবে এবং নিজের অ্যাক্সেস অবস্থা দেখতে পারবে।",
      "",
      "🎟 <b>Generate Access Code</b> — নতুন ইনস্টলেশনের জন্য কোড",
      "📊 <b>My Access Status</b> — activate আছে কি না দেখা",
      "",
      "<i>কোড কেবল একটি ইনস্টলেশনে কাজ করবে, আর মেয়াদ ৫ মিনিট।</i>",
    ].join("\n"), { reply_markup: MENU });
    return new Response(JSON.stringify({ ok: true }));
  }

  if (cmd === "/status") {
    const out = await api({ action: "status", tg_user_id: uid });
    if (dbg) return new Response(JSON.stringify({ ok: true, api: out }));
    await send(chatId, statusCard({ ...out, telegram_user_id: uid }), { reply_markup: MENU });
    return new Response(JSON.stringify({ ok: true }));
  }

  if (cmd === "/help") {
    await send(chatId, "🎟 কোড নিতে <b>Generate Access Code</b> চাপো।\n📊 নিজের অবস্থা দেখতে <b>My Access Status</b> চাপো।", { reply_markup: MENU });
    return new Response(JSON.stringify({ ok: true }));
  }

  // ---------- owner console ----------
  if (["/admin", "/find", "/revoke", "/reset", "/block", "/unblock", "/config", "/history"].includes(cmd)) {
    if (uid !== OWNER_ID) {
      await send(chatId, "শুধু owner পারবে।");
      return new Response(JSON.stringify({ ok: true }));
    }

    if (cmd === "/admin") {
      const out = await api({ action: "events", actor_tg_id: uid });
      const ttl = (await svc.entities.LibSetting.filter({ key: "code_ttl_minutes" }, { limit: 1 }));
      const ttlRow = (ttl?.items ?? ttl ?? [])[0];
      if (dbg) return new Response(JSON.stringify({ ok: true, api: out, ttl: ttlRow?.value ?? "5" }));
      const recent = (out.recent ?? []).slice(0, 5).map((r: any) =>
        `• <code>${esc(r.installation)}</code> · ${esc(String(r.tg))} · ${r.status}`);
      await send(chatId, [
        "🛠 <b>ADMIN CONSOLE</b>",
        "",
        `👥 Users: <b>${out.users ?? 0}</b> (blocked ${out.blocked ?? 0})`,
        `📱 Installations: <b>${out.installations ?? 0}</b> — 🟢 ${out.active ?? 0} · 🚫 ${out.revoked ?? 0}`,
        `🔑 Activations: <b>${out.activations ?? 0}</b>`,
        `⏳ Code TTL: <b>${ttlRow?.value ?? "5"}</b> min`,
        "",
        "<b>Recent activations</b>",
        ...(recent.length ? recent : ["• none yet"]),
        "",
        "/find &lt;tgid&gt; · /revoke &lt;installation|code&gt; · /reset &lt;installation&gt;",
        "/block &lt;tgid&gt; · /unblock &lt;tgid&gt; · /config &lt;key&gt; &lt;value&gt;",
      ].join("\n"));
      return new Response(JSON.stringify({ ok: true }));
    }

    if (cmd === "/find") {
      const out = await api({ action: "status", tg_user_id: args[0] ?? "" });
      if (dbg) return new Response(JSON.stringify({ ok: true, api: out }));
      await send(chatId, statusCard(out));
      return new Response(JSON.stringify({ ok: true }));
    }

    if (cmd === "/revoke" || cmd === "/reset") {
      const arg = args[0] ?? "";
      if (!arg) {
        await send(chatId, `ব্যবহার: ${cmd} &lt;installation-id${cmd === "/revoke" ? "|NAI-code" : ""}&gt;`);
        return new Response(JSON.stringify({ ok: true }));
      }
      const payload: any = { action: cmd.slice(1), actor_tg_id: uid };
      if (cmd === "/revoke" && arg.toUpperCase().startsWith("NAI-")) payload.code = arg;
      else payload.installation_id = arg;
      const out = await api(payload);
      if (dbg) return new Response(JSON.stringify({ ok: true, api: out }));
      await send(chatId, out.ok
        ? (cmd === "/revoke" ? `🚫 বাতিল হয়েছে: ${esc(out.installation ?? arg)}` : "♻️ Reset হয়েছে — পরের বার নতুন কোড লাগবে।")
        : `⚠️ হয়নি: ${esc(out.message ?? out.status ?? "not found")}`);
      return new Response(JSON.stringify({ ok: true }));
    }

    if (cmd === "/block" || cmd === "/unblock") {
      const out = await api({ action: "block", actor_tg_id: uid, tg_user_id: args[0] ?? "", blocked: cmd === "/block" });
      if (dbg) return new Response(JSON.stringify({ ok: true, api: out }));
      await send(chatId, out.ok
        ? (cmd === "/block" ? "🚫 ইউজার ব্লক হয়েছে।" : "✅ ব্লক তুলে নেওয়া হয়েছে।")
        : "⚠️ ইউজার পাওয়া যায়নি।");
      return new Response(JSON.stringify({ ok: true }));
    }

    if (cmd === "/config") {
      const [key, ...rest] = args;
      const value = rest.join(" ");
      if (!key || !value) {
        await send(chatId, "ব্যবহার: /config code_ttl_minutes 5");
        return new Response(JSON.stringify({ ok: true }));
      }
      const out = await api({ action: "config", actor_tg_id: uid, key, value });
      if (dbg) return new Response(JSON.stringify({ ok: true, api: out }));
      await send(chatId, out.ok ? `⚙️ ${esc(key)} = <b>${esc(value)}</b>` : "⚠️ হয়নি।");
      return new Response(JSON.stringify({ ok: true }));
    }

    if (cmd === "/history") {
      const rows = (await svc.entities.AccessCode.list("-created_date", 100));
      const list = rows?.items ?? rows ?? [];
      const lines = list.slice(0, 12).map((r: any) => {
        const icon = r.status === "bound" ? "🔒" : r.status === "revoked" ? "🚫" : r.status === "expired" ? "⌛" : "🕓";
        return `${icon} ${esc(r.tg_name || r.tg_id || "?")} · ${when(r.issued_at)}`;
      });
      if (dbg) return new Response(JSON.stringify({ ok: true, count: list.length }));
      await send(chatId, [
        "🎫 <b>CODE HISTORY</b>",
        `<i>${list.length} codes · plain codes are never stored</i>`,
        "",
        ...(lines.length ? lines : ["• none yet"]),
      ].join("\n"));
      return new Response(JSON.stringify({ ok: true }));
    }
  }

  await send(chatId, "কমান্ড বুঝিনি। /start লেখো।", { reply_markup: MENU });
  return new Response(JSON.stringify({ ok: true }));
});
