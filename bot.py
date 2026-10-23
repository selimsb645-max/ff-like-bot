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
import httpx
from telebot import types
import ff_bd_api

BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN_2", "")
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
USAGE_FILE = Path(__file__).parent / "like_usage.json"
USAGE_LOCK = threading.Lock()
MAX_LIKES_PER_REQUEST = 10
ADMIN_IDS = set(int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip())


def persist_usage_github():
    """Commit usage.json back to the repo so dedup history survives runner restarts."""
    tok = os.environ.get("GITHUB_USAGE_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo:
        return
    try:
        import base64
        api = f"https://api.github.com/repos/{repo}/contents/usage.json"
        r = httpx.get(api, headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        sha = r.json().get("sha") if r.status_code == 200 else None
        content = base64.b64encode(open(USAGE_FILE, "rb").read()).decode()
        httpx.put(api, headers={"Authorization": f"Bearer {tok}"}, timeout=15,
                  json={"message": "update like usage", "content": content, "sha": sha})
    except Exception as e:
        print("usage persist failed:", e)


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
        "<b>/pool</b> — বটের লাইক পুল স্ট্যাটাস\n"
        "<b>/guests</b> — কতগুলো গেস্ট অ্যাকাউন্ট বানানো হয়েছে\n\n"
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
    # আগের লাইক কাউন্ট
    try:
        s0 = ff_bd_api.get_stats(target)
        likes_before = s0.get("likes") if s0 else None
    except Exception:
        likes_before = None
    try:
        sent = ff_bd_api.send_like(target, count=count, used=used)
    except Exception as e:
        bot.edit_message_text(f"❌ এরর: {e}", msg.chat.id, msg.message_id)
        return
    with USAGE_LOCK:
        usage["used_pairs"] = [list(k) for k in used]
        save_usage(usage)
    threading.Thread(target=persist_usage_github, daemon=True).start()
    # পরের লাইক কাউন্ট
    time.sleep(3)
    try:
        s1 = ff_bd_api.get_stats(target)
        likes_after = s1.get("likes") if s1 else None
    except Exception:
        likes_after = None

    if sent == 0:
        bot.edit_message_text("⚠️ পুলের সব অ্যাকাউন্ট এই টার্গেটে লাইক দিয়ে দিয়েছে। পরে আবার চেষ্টা করুন।",
                              msg.chat.id, msg.message_id)
        return
    delta = None
    if isinstance(likes_before, int) and isinstance(likes_after, int):
        delta = likes_after - likes_before
    if delta == 0:
        text = ("❌ লাইক যায়নি — সার্ভারে কাউন্ট বাড়েনি "
                f"(আগে {likes_before}, এখনো {likes_after})।\n"
                "সাধারণ কারণ: পুলের অ্যাকাউন্টগুলো আগেই এই প্রোফাইলে লাইক দিয়েছে, "
                "আর এক অ্যাকাউন্ট এক প্রোফাইলে একবারই লাইক দিতে পারে।")
    elif delta is not None and delta < sent:
        text = (f"⚠️ {sent}টা পাঠানোর চেষ্টা হয়েছে, আসলে গেছে {delta}টা।\n"
                f"❤️ এখন মোট Likes: <b>{likes_after}</b>")
    elif delta is not None:
        text = (f"✅ {delta}টা লাইক যোগ হয়েছে!\n"
                f"❤️ এখন মোট Likes: <b>{likes_after}</b>")
    else:
        text = (f"✅ {sent}টা রিকোয়েস্ট সফল হয়েছে, তবে ভেরিফাই করা গেল না।\n"
                f"/stats {target} দিয়ে নিজে চেক করুন।")
    bot.edit_message_text(text, msg.chat.id, msg.message_id)


@bot.message_handler(commands=["guests"])
def cmd_guests(m):
    msg = bot.reply_to(m, "🔄 গেস্ট অ্যাকাউন্ট গুনছি...")
    try:
        import base64
        r = httpx.get(
            "https://api.github.com/repos/selimsb645-max/ff-like-bot/contents/guests_manager/guests.json",
            headers={"Accept": "application/vnd.github+json"}, timeout=20)
        r.raise_for_status()
        data = json.loads(base64.b64decode(r.json()["content"]))
    except Exception as e:
        bot.edit_message_text(f"❌ এরর: {e}", msg.chat.id, msg.message_id)
        return
    n = len(data)
    last_uid = data[-1].get("uid", "?") if data else "?"
    bar_len = min(n * 10 // 200, 10)
    bar = "▰" * bar_len + "▱" * (10 - bar_len)
    bot.edit_message_text(
        f"👥 গেস্ট অ্যাকাউন্ট বানানো হয়েছে: <b>{n}/200</b>\n"
        f"{bar} <b>{min(n * 100 // 200, 100)}%</b>\n"
        f"🔔 সর্বশেষ UID: <code>{last_uid}</code>\n"
        f"⚡ জেনারেশন চলমান — কাউন্ট বাড়তেই থাকবে।",
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
