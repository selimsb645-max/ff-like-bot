// ============================================================
// FF ACCESS BOT — channel gate, then a readable access code
// Bot: @NAI_AccessBot (token TELEGRAM_BOT_TOKEN_6)
//
// /start  → if the person is not in @FF_FreeLike_BD, they get a
//           message with two buttons: join the channel, and ✅ ভেরিফাই.
//           Tapping verify re-checks membership; only then is a code
//           (NAI-XXXX-XXXX-XXXX, 5 min, single-use) handed over and
//           the message turns into "✅ ভেরিফাইড".
// Already a member → code straight away.
//
// The code is stored in AccessCode as "issued"; ffAccessRedeem later
// welds it to one device + one public IP.
// Webhook: /functions/ffAccessBot   (Telegram secret header checked)
// ============================================================
import { createClientFromRequest } from "npm:@base44/sdk@0.8.54";

const BOT_TOKEN = Deno.env.get("TELEGRAM_BOT_TOKEN_6") ?? "";
// Environment-only. This gateway authenticates Telegram's webhook header. The
// variable it reads is the exact value that used to be hardcoded here, so no
// client changes; a missing secret refuses every call instead of accepting one.
const WEBHOOK_SECRET = Deno.env.get("NAI_INTERNAL_SECRET") ?? "";
const CHANNEL = "@FF_FreeLike_BD";
const OWNER_ID = "8012564523";                  // only the owner may revoke
const CHANNEL_ID = "-1004394321090";
const CHANNEL_URL = "https://t.me/FF_FreeLike_BD";
const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // no 0 O 1 I
const TTL_MINUTES = 5;

const API = `https://api.telegram.org/bot${BOT_TOKEN}`;

async function tg(method: string, body: Record<string, unknown>) {
  const res = await fetch(`${API}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return await res.json();
}

function block(len: number): string {
  let out = "";
  const buf = crypto.getRandomValues(new Uint8Array(len));
  for (let i = 0; i < len; i++) out += ALPHABET[buf[i] % ALPHABET.length];
  return out;
}

function newCode(): string {
  return `NAI-${block(4)}-${block(4)}-${block(4)}`;
}

/** Telegram display names may contain <, & or > — never let them break the HTML. */
function htmlEsc(v: string): string {
  return String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** true when the user has really joined the channel. */
async function isMember(userId: string): Promise<{ member: boolean; error?: string }> {
  const r = await tg("getChatMember", { chat_id: CHANNEL_ID, user_id: Number(userId) });
  if (!r?.ok) {
    const desc = String(r?.description ?? "");
    // a user Telegram cannot resolve cannot be a member of the channel
    if (desc.includes("PARTICIPANT_ID_INVALID") || desc.includes("user not found")) {
      return { member: false };
    }
    return { member: false, error: desc || "membership check failed" };
  }
  const status = String(r.result?.status ?? "");
  const member = ["creator", "administrator", "member"].includes(status) ||
    (status === "restricted" && r.result?.is_member === true);
  return { member };
}

async function mintAndSend(svc: any, chatId: number, tgId: string, name: string) {
  const base44 = svc;
  const code = newCode();
  const now = new Date().toISOString();
  await base44.entities.AccessCode.create({
    code,
    status: "issued",
    tg_id: tgId,
    tg_name: name,
    device: "",
    ip: "",
    issued_at: now,
    bound_at: "",
    uses: 0,
    note: "via channel gate",
  });
  const text = [
      "✅ <b>ACCESS VERIFIED SUCCESSFULLY</b>",
      "",
      `𖣘 亗 ${htmlEsc(name)} 亗 𖣘`,
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
      `⏳ <b>Validity:</b> মাত্র ${TTL_MINUTES} মিনিট।`,
      "",
      "📱 <b>Device Lock:</b> প্রথম যে ফোনে কোডটি ব্যবহার করা হবে, সেটির সাথেই লক হয়ে যাবে।",
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

  const sent = await tg("sendMessage", {
    chat_id: chatId,
    text,
    parse_mode: "HTML",
    link_preview_options: { is_disabled: true },
  });
  if (!sent?.ok) {
    // never lose a code because of a formatting problem
    await tg("sendMessage", { chat_id: chatId, text: text.replace(/<[^>]+>/g, "") });
  }
  return code;
}

Deno.serve(async (req) => {

  if (req.method !== "POST") {
    return new Response(JSON.stringify({ ok: true, service: "ff-access-bot" }), {
      headers: { "Content-Type": "application/json" },
    });
  }
  if (!WEBHOOK_SECRET || req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== WEBHOOK_SECRET) {
    return new Response(JSON.stringify({ error: "unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  let update: any = {};
  try {
    update = await req.json();
  } catch {
    return new Response(JSON.stringify({ ok: true }));
  }

  // ---------- the ✅ ভেরিফাই button ----------
  if (update.callback_query) {
    const cb = update.callback_query;
    const userId = String(cb.from?.id ?? "");
    const name = [cb.from?.first_name, cb.from?.last_name].filter(Boolean).join(" ") || "friend";
    const chatId = cb.message?.chat?.id;

    // membership is always re-checked here; the button proves nothing
    const { member, error } = await isMember(userId);

    if (update.debug === true) {
      return new Response(JSON.stringify({ ok: true, member, error: error ?? null }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    if (!member) {
      await tg("answerCallbackQuery", {
        callback_query_id: cb.id,
        text: error
          ? "চ্যানেল চেক করা যাচ্ছে না। একটু পরে আবার চেষ্টা করো।"
          : "আগে চ্যানেলে জয়েন করো, তারপর আবার ভেরিফাই চাপো।",
        show_alert: true,
      });
      return new Response(JSON.stringify({ ok: true }));
    }

    await tg("answerCallbackQuery", { callback_query_id: cb.id, text: "✅ ভেরিফাইড" });
    try {
      await tg("editMessageText", {
        chat_id: chatId,
        message_id: cb.message?.message_id,
        text: "✅ <b>ভেরিফাইড</b> — তুমি চ্যানেলে জয়েন আছো।",
        parse_mode: "HTML",
      });
    } catch (_e) { /* message may be too old to edit; harmless */ }

    await mintAndSend(createClientFromRequest(req).asServiceRole, chatId, userId, name);
    return new Response(JSON.stringify({ ok: true }));
  }

  // ---------- /start and /code ----------
  const msg = update.message ?? update.edited_message;
  if (!msg?.chat?.id) return new Response(JSON.stringify({ ok: true }));

  const chatId = msg.chat.id;
  const tgId = String(msg.from?.id ?? chatId);
  const name = [msg.from?.first_name, msg.from?.last_name].filter(Boolean).join(" ") || "friend";
  const text = String(msg.text ?? "").trim();
  const isStart = text.startsWith("/start") || text === "";

  // ---------- owner-only: /history — one card per code, premium look ----------
  if (text.startsWith("/history")) {
    if (tgId !== OWNER_ID) {
      await tg("sendMessage", { chat_id: chatId, text: "শুধু owner দেখতে পারবে।" });
      return new Response(JSON.stringify({ ok: true }));
    }

    // /history · /history 2 · /history bound · /history failed 3
    const args = text.split(/\s+/).slice(1).map((a) => a.toLowerCase()).filter(Boolean);
    const statuses = ["issued", "bound", "revoked"];
    const want = args.find((a) => statuses.includes(a)) ?? "";
    const pageArg = args.find((a) => /^[0-9]+$/.test(a));
    const page = Math.max(1, Number(pageArg ?? 1));

    const svc = createClientFromRequest(req).asServiceRole;
    let rows: any[] = [];
    try {
      const listed = await svc.entities.AccessCode.list("-created_date", 200);
      rows = listed.items ?? listed ?? [];
    } catch (_e) {
      rows = [];
    }

    const count = (st: string) => rows.filter((r: any) => r.status === st).length;
    const bound = count("bound"), waiting = count("issued"), dead = count("revoked");
    if (want) rows = rows.filter((r: any) => r.status === want);

    // ---- small formatting helpers ----
    const BN = ["০", "১", "২", "৩", "৪", "৫", "৬", "৭", "৮", "৯"];
    const bn = (v: string | number) => String(v).replace(/[0-9]/g, (d) => BN[Number(d)]);
    const MON = ["জানু", "ফেব", "মার্চ", "এপ্রি", "মে", "জুন", "জুল", "আগ", "সেপ", "অক্টো", "নভে", "ডিসে"];
    const dhaka = (iso: string) => {
      if (!iso) return "—";
      const t = new Date(iso);
      if (isNaN(t.getTime())) return "—";
      const p = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Dhaka", day: "2-digit", month: "2-digit", year: "numeric",
        hour: "2-digit", minute: "2-digit", hour12: true,
      }).formatToParts(t).reduce((acc: any, x) => (acc[x.type] = x.value, acc), {});
      const mon = MON[Number(p.month) - 1] ?? p.month;
      const ap = String(p.dayPeriod ?? "").toUpperCase();
      return `${bn(p.day)} ${mon}, ${bn(p.hour)}:${bn(p.minute)} ${ap}`.trim();
    };
    const esc = (v: string) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    // ---- one card per code ----
    const card = (r: any) => {
      const live = r.status === "bound";
      const dead2 = r.status === "revoked";
      const icon = dead2 ? "🚫" : live ? "🔒" : "🕓";
      const label = dead2 ? "বাতিল" : live ? "ফোনে বাঁধা" : "অপেক্ষায়";
      const owner = esc(String(r.tg_name || r.tg_id || "অজানা"));
      const phone = r.device
        ? `<code>${esc(String(r.device).slice(0, 6))}</code>`
        : "<i>এখনো কোনো ফোনে নেই</i>";
      const made = dhaka(String(r.issued_at || r.created_date || ""));
      const welded = r.bound_at ? dhaka(String(r.bound_at)) : "";
      const lines = [
        `${icon} <b>${esc(String(r.code ?? ""))}</b>`,
        "",
        `👤 নিয়েছে: <b>${owner}</b>${r.tg_id ? ` · <code>${esc(String(r.tg_id))}</code>` : ""}`,
        `📱 ফোন: ${phone}`,
        `🕓 তৈরি: ${made}`,
      ];
      if (welded) lines.push(`🔗 বাঁধা: ${welded}`);
      if (r.ip) lines.push(`🌐 আইপি: <code>${esc(String(r.ip))}</code>`);
      lines.push(`📌 অবস্থা: ${icon} ${label}${r.uses ? ` · ${bn(r.uses)} বার` : ""}`);
      return `<blockquote>${lines.join("\n")}</blockquote>`;
    };

    const total = rows.length;
    const PER = 5;
    const pages = Math.max(1, Math.ceil(total / PER));
    const here = Math.min(page, pages);
    const slice = rows.slice((here - 1) * PER, here * PER);

    const head = [
      "🎫 <b>অ্যাক্সেস কোড হিস্টরি</b>",
      `<i>${want ? `ফিল্টার: ${want === "bound" ? "ফোনে বাঁধা" : want === "issued" ? "অপেক্ষায়" : "বাতিল"}` : "সব কোড"}</i>`,
      "",
      `সক্রিয় 🔒 <b>${bn(bound)}</b>   অপেক্ষায় 🕓 <b>${bn(waiting)}</b>   বাতিল 🚫 <b>${bn(dead)}</b>`,
      `মোট <b>${bn(rows.length)}</b> কোড · পেজ <b>${bn(here)}</b>/${bn(pages)}`,
    ];

    const body = slice.length ? slice.map(card).join("\n") : "<i>কোনো কোড নেই।</i>";
    const nav = pages > 1
      ? [`<i>পরের পেজ: /history${want ? " " + want : ""} ${bn(Math.min(here + 1, pages))}</i>`]
      : [];
    const out = [...head, "", body, ...nav].join("\n");   // body is a string: do not spread it

    if (update.debug === true) {
      return new Response(JSON.stringify({ ok: true, text: out.replace(/<[^>]+>/g, "").replace(/&amp;/g, "&") }), {
        headers: { "Content-Type": "application/json" },
      });
    }
    await tg("sendMessage", {
      chat_id: chatId,
      text: out,
      parse_mode: "HTML",
      link_preview_options: { is_disabled: true },
    });
    return new Response(JSON.stringify({ ok: true }));
  }

  // ---------- owner-only: revoke a code / a phone ----------
  if (text.startsWith("/revoke")) {
    if (tgId !== OWNER_ID) {
      await tg("sendMessage", { chat_id: chatId, text: "শুধু owner পারবে।" });
      return new Response(JSON.stringify({ ok: true }));
    }
    const arg = text.split(/\s+/)[1]?.toUpperCase() ?? "";
    if (!arg) {
      await tg("sendMessage", {
        chat_id: chatId,
        text: "কোডসহ লেখো: <code>/revoke NAI-XXXX-XXXX-XXXX</code>",
        parse_mode: "HTML",
      });
      return new Response(JSON.stringify({ ok: true }));
    }
    const svc = createClientFromRequest(req).asServiceRole;
    const found = await svc.entities.AccessCode.filter({ code: arg }, { limit: 5 });
    const rec = (found.items ?? found ?? [])[0];
    if (!rec) {
      await tg("sendMessage", { chat_id: chatId, text: `কোডটা পাওয়া যায়নি: ${arg}` });
      return new Response(JSON.stringify({ ok: true }));
    }
    await svc.entities.AccessCode.update(rec.id, { status: "revoked" });
    await tg("sendMessage", {
      chat_id: chatId,
      text: `🚫 বাতিল করা হয়েছে: <code>${rec.code}</code>${rec.tg_name ? ` (${rec.tg_name})` : ""}\nওই ফোন আর চালু হবে না।`,
      parse_mode: "HTML",
    });
    return new Response(JSON.stringify({ ok: true }));
  }

  if (!isStart && !text.startsWith("/code")) {
    await tg("sendMessage", {
      chat_id: chatId,
      text: "কোড নিতে /start লেখো।\n\nSend /start to get an access code.",
    });
    return new Response(JSON.stringify({ ok: true }));
  }

  const { member, error } = await isMember(tgId);

  if (update.debug === true) {
    return new Response(JSON.stringify({ ok: true, member, error: error ?? null }), {
      headers: { "Content-Type": "application/json" },
    });
  }

  if (member) {
    await mintAndSend(createClientFromRequest(req).asServiceRole, chatId, tgId, name);
    return new Response(JSON.stringify({ ok: true }));
  }

  await tg("sendMessage", {
    chat_id: chatId,
    text: [
      `👋 <b>${name}</b>, কোড পেতে হলে আগে আমাদের চ্যানেলে জয়েন করতে হবে।`,
      "",
      `1️⃣ নিচের <b>চ্যানেলে জয়েন করো</b> বাটনে চাপো`,
      `2️⃣ চ্যানেলে ঢুকে <b>Join</b> চাপো`,
      `3️⃣ ফিরে এসে <b>✅ ভেরিফাই</b> বাটনে চাপো`,
      "",
      error ? `⚠️ চেক করা যাচ্ছে না: ${error}` : "",
    ].filter(Boolean).join("\n"),
    parse_mode: "HTML",
    reply_markup: {
      inline_keyboard: [
        [{ text: "📢 চ্যানেলে জয়েন করো", url: CHANNEL_URL }],
        [{ text: "✅ ভেরিফাই", callback_data: "vf" }],
      ],
    },
  });

  return new Response(JSON.stringify({ ok: true }));
});
