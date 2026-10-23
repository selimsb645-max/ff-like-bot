#!/usr/bin/env python3
"""
FF BD Telegram Bot — stats + like automation.
Setup: get a token from @BotFather, then:
  export BOT_TOKEN=123456:ABC...
  python3 bot.py
"""
import os, json, time, threading
from pathlib import Path
import telebot
from telebot import types
import ff_bd_api

BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN_2", "")
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
USAGE_FILE = Path(__file__).parent / "like_usage.json"
USAGE_LOCK = threading.Lock()
MAX_LIKES_PER_REQUEST = 10
ADMIN_IDS = set(int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip())


def load_usage() -> dict:
    if USAGE_FILE.exists():
        try:
            return json.loads(USAGE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_usage(u: dict):
    USAGE_FILE.write_text(json.dumps(u, indent=1))


@bot.message_handler(commands=["start", "help"])
def cmd_start(m):
    bot.reply_to(m,
        "<b>🔥 FF BD Bot</b>\n\n"
        "BD সার্ভারের জন্য স্ট্যাটস আর লাইক বট।\n\n"
        "<b>/stats</b> <code>UID</code> — প্লেয়ার প্রোফাইল (নাম, লাইক)\n"
        "<b>/like</b> <code>UID [count]</code> — লাইক পাঠান\n"
        "<b>/pool</b> — বটের লাইক পুল স্ট্যাটাস\n\n"
        "UID = গেমের প্রোফাইলে যে নম্বর দেখায়।")


@bot.message_handler(commands=["stats"])
def cmd_stats(m):
    parts = m.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(m, "⚠️ ঠিকভাবে লিখুন: <code>/stats 18328637549</code>")
        return
    uid = int(parts[1])
    msg = bot.reply_to(m, "🔄 খোঁজা হচ্ছে...")
    try:
        s = ff_bd_api.get_stats(uid)
    except Exception as e:
        bot.edit_message_text(f"❌ এরর: {e}", msg.chat.id, msg.message_id)
        return
    if not s or s.get("error"):
        bot.edit_message_text(f"❌ {s.get('error') if s else 'এরর'}\nUID ঠিক আছে? BD সার্ভারের প্লেয়ার তো?",
                             msg.chat.id, msg.message_id)
        return
    bot.edit_message_text(
        f"👤 <b>{s['name']}</b>\n"
        f"🆔 UID: <code>{s['uid']}</code>\n"
        f"❤️ Likes: <b>{s['likes']}</b>",
        msg.chat.id, msg.message_id)


@bot.message_handler(commands=["like"])
def cmd_like(m):
    parts = m.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(m, "⚠️ ঠিকভাবে লিখুন: <code>/like 18328637549 5</code>")
        return
    target = int(parts[1])
    count = min(int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 5, MAX_LIKES_PER_REQUEST)
    msg = bot.reply_to(m, f"🔄 {target} কে {count}টা লাইক পাঠানো হচ্ছে...")
    usage = load_usage()
    with USAGE_LOCK:
        used = set(tuple(k) for k in usage.get("used_pairs", []))
    try:
        sent = ff_bd_api.send_like(target, count=count, used=used)
    except Exception as e:
        bot.edit_message_text(f"❌ এরর: {e}", msg.chat.id, msg.message_id)
        return
    with USAGE_LOCK:
        usage["used_pairs"] = [list(k) for k in used]
        save_usage(usage)
    s = ff_bd_api.get_stats(target)
    likes_now = s.get("likes", "?") if s else "?"
    if sent == 0:
        bot.edit_message_text("⚠️ পুলের সব অ্যাকাউন্ট এই টার্গেটে লাইক দিয়ে দিয়েছে। পরে আবার চেষ্টা করুন।",
                              msg.chat.id, msg.message_id)
    else:
        bot.edit_message_text(
            f"✅ {sent}টা লাইক পাঠানো হয়েছে!\n"
            f"❤️ এখন মোট Likes: <b>{likes_now}</b>",
            msg.chat.id, msg.message_id)


@bot.message_handler(commands=["pool"])
def cmd_pool(m):
    pool = ff_bd_api.get_pool()
    fresh = sum(1 for t in pool if t.get("exp", 0) > time.time())
    bot.reply_to(m, f"🔋 লাইক পুল: <b>{len(pool)}</b> অ্যাকাউন্ট ({fresh} টোকেন এখনো ভ্যালিড)")


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("BOT_TOKEN env var নেই! @BotFather থেকে টোকেন নিন আর চালান:")
        print("  export BOT_TOKEN=123:ABC... && python3 bot.py")
        raise SystemExit(1)
    print("bot running...")
    bot.infinity_polling(skip_pending=True)
