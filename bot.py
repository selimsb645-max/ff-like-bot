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
MAX_LIKES_PER_REQUEST = 500
ADMIN_IDS = set(int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip())


def _github_commit_file(fname: str, message: str):
    """Commit a local bot-state file to the repo (survives runner restarts)."""
    tok = os.environ.get("GITHUB_USAGE_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo:
        return
    try:
        import base64
        path = Path(__file__).parent / fname
        api = f"https://api.github.com/repos/{repo}/contents/{fname}"
        r = httpx.get(api, headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        sha = r.json().get("sha") if r.status_code == 200 else None
        content = base64.b64encode(path.read_bytes()).decode()
        httpx.put(api, headers={"Authorization": f"Bearer {tok}"}, timeout=15,
                  json={"message": message, "content": content, "sha": sha})
    except Exception as e:
        print(f"persist {fname} failed:", e)


def persist_usage_github():
    _github_commit_file(USAGE_FILE.name, "update like usage")
    _github_commit_file(HISTORY_FILE.name, "update like history")
    _github_commit_file(REMINDERS_FILE.name, "update reminders")
    _github_commit_file("dead_tokens.json", "update dead token blacklist")


def load_usage() -> dict:
    if USAGE_FILE.exists():
        try:
            return json.loads(USAGE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_usage(u: dict):
    USAGE_FILE.write_text(json.dumps(u, indent=1))


HISTORY_FILE = Path(__file__).parent / "history.json"
REMINDERS_FILE = Path(__file__).parent / "reminders.json"


def _load_json(p: Path) -> dict:
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _save_json(p: Path, d: dict):
    p.write_text(json.dumps(d, indent=1))


def record_history(user_id, username, target, requested, landed):
    hist = _load_json(HISTORY_FILE)
    users = hist.setdefault("users", {})
    entry = {"uid": str(target), "requested": requested, "landed": landed,
             "ts": int(time.time()), "name": username or ""}
    users.setdefault(str(user_id), []).append(entry)
    _save_json(HISTORY_FILE, hist)


def set_reminder(user_id, username, target, seconds_later):
    rem = _load_json(REMINDERS_FILE)
    rem[str(user_id)] = {"uid": str(target), "username": username or "",
                         "remind_at": int(time.time() + seconds_later),
                         "saved_at": int(time.time())}
    _save_json(REMINDERS_FILE, rem)


def cancel_reminder(user_id):
    rem = _load_json(REMINDERS_FILE)
    if str(user_id) in rem:
        del rem[str(user_id)]
        _save_json(REMINDERS_FILE, rem)
        return True
    return False


REFS_FILE = Path(__file__).parent / "refs.json"
PENDING = {}  # user_id -> ("like"|"stats"|"profile", ts)
LAST_UID = {}  # user_id -> last UID they sent


def get_saved_uid(user_id) -> str | None:
    rem = _load_json(REMINDERS_FILE)
    info = rem.get(str(user_id))
    return info.get("uid") if info else None


def build_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💖 Fast Like", callback_data="menu:like"),
        types.InlineKeyboardButton("📊 My Stats", callback_data="menu:stats"),
    )
    kb.add(
        types.InlineKeyboardButton("👤 Profile", callback_data="menu:profile"),
        types.InlineKeyboardButton("📜 History", callback_data="menu:history"),
    )
    kb.add(
        types.InlineKeyboardButton("⚙️ My UID", callback_data="menu:myuid"),
        types.InlineKeyboardButton("🔗 Share & Refer", callback_data="menu:share"),
    )
    return kb


def build_uid_card(user_id):
    saved = get_saved_uid(user_id)
    rem = _load_json(REMINDERS_FILE).get(str(user_id), {})
    saved_at = rem.get("saved_at")
    if saved and str(saved).isdigit():
        txt = (f"⚙️ <b>আমার UID</b>\n\n"
               f"🆔 সেভ করা UID: <code>{saved}</code>\n")
        if saved_at:
            import datetime
            txt += f"📅 সেভ করা হয়েছে: {datetime.datetime.fromtimestamp(saved_at).strftime('%d %b %Y, %H:%M')}\n"
        txt += "\nএখন <code>/like</code> বা <code>/stats</code> লিখলেই এই UID-তে কাজ হবে।"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("✏️ UID পরিবর্তন করুন", callback_data="uid:change"),
               types.InlineKeyboardButton("🗑️ UID মুছে ফেলুন", callback_data="uid:clear"))
        return txt, kb
    return ("⚠️ আপনার কোনো UID সেভ করা নেই।\n"
            "✏️ UID পরিবর্তন করুন বাটনে চাপ দিয়ে নতুন UID সেভ করুন।", None)


def run_like_flow(chat_id, target, user, skip_slot=False):
    """Execute a full-pool like for target; report honestly; record history+reminder."""
    if not skip_slot:
        ok, wait = _like_slot_free(user.id)
        if not ok:
            try:
                bot.send_message(chat_id, f"⏳ লাইক সিস্টেম ব্যস্ত / কুলডাউনে। *{wait} সেকেন্ড* পরে আবার চেষ্টা করুন।")
            except Exception:
                pass
            return
    try:
        pool = ff_bd_api.get_pool()
        pool_size = len(pool)
        jwt = pool[0]["token"] if pool else None
    except Exception:
        pool_size, jwt = MAX_LIKES_PER_REQUEST, None
    usage = load_usage()
    with USAGE_LOCK:
        used = set(tuple(k) for k in usage.get("used_pairs", []))
    try:
        s0 = ff_bd_api.get_stats(target, jwt)
        likes_before = s0.get("likes") if s0 else None
        sent = ff_bd_api.send_like(target, count=pool_size, used=used)
    except Exception as e:
        bot.send_message(chat_id, f"❌ এরর: {e}")
        return
    with USAGE_LOCK:
        usage["used_pairs"] = [list(k) for k in used]
        save_usage(usage)
    threading.Thread(target=persist_usage_github, daemon=True).start()
    time.sleep(3)
    try:
        s1 = ff_bd_api.get_stats(target, jwt)
        likes_after = s1.get("likes") if s1 else None
    except Exception:
        likes_after = None
    delta = (likes_after - likes_before) if isinstance(likes_before, int) and isinstance(likes_after, int) else None
    try:
        record_history(user.id, user.first_name or "", target,
                       pool_size, delta if isinstance(delta, int) and delta > 0 else sent)
        if delta is None or delta > 0:
            set_reminder(user.id, user.first_name or "", target, 24 * 3600)
    except Exception:
        pass
    if sent == 0:
        bot.send_message(chat_id, "⚠️ পুলের সব অ্যাকাউন্ট এই টার্গেটে লাইক দিয়ে দিয়েছে। কাল আবার চেষ্টা করুন।")
    elif delta == 0:
        bot.send_message(chat_id, "⚠️ আজ এই UID-তে ইতিমধ্যে লাইক দেওয়া হয়েছে — কাল আবার চেষ্টা করুন।")
    elif delta is not None and 0 < delta < sent:
        bot.send_message(chat_id, f"⚠️ {sent}টা পাঠানোর চেষ্টা হয়েছে, আসলে গেছে {delta}টা।\n"
                                  f"❤️ এখন মোট Likes: <b>{likes_after}</b>")
    elif delta is not None and delta > 0:
        bot.send_message(chat_id, f"✅ {delta}টা লাইক যোগ হয়েছে!\n"
                                  f"❤️ এখন মোট Likes: <b>{likes_after}</b>")
    else:
        bot.send_message(chat_id, f"✅ {sent}টা রিকোয়েস্ট সফল। /stats {target} দিয়ে চেক করুন।")


def run_stats_flow(chat_id, target, user):
    try:
        pool = ff_bd_api.get_pool()
        jwt = pool[0]["token"] if pool else None
        s = ff_bd_api.get_stats(target, jwt)
    except Exception as e:
        bot.send_message(chat_id, f"❌ এরর: {e}")
        return
    if not s:
        bot.send_message(chat_id, "❌ প্লেয়ার পাওয়া যায়নি।")
    elif s.get("error"):
        bot.send_message(chat_id, f"❌ {s['error']}")
    else:
        bot.send_message(chat_id,
            f"👤 <b>{s.get('name') or '-'}</b>\n🆔 UID: <code>{s.get('uid')}</code>\n"
            f"⭐ লেভেল: <b>{s.get('level') or '-'}</b>\n❤️ Likes: <b>{s.get('likes')}</b>")


def run_profile_flow(chat_id, target, user):
    try:
        pool = ff_bd_api.get_pool()
        jwt = pool[0]["token"] if pool else None
        p = ff_bd_api.get_profile(target, jwt)
    except Exception as e:
        bot.send_message(chat_id, f"❌ এরর: {e}")
        return
    if not p or p.get("error"):
        bot.send_message(chat_id, "❌ প্রোফাইল আনা যায়নি।")
        return
    g = p.get("guild")
    card = ("👤 <b>প্লেয়ার প্রোফাইল</b>\n━━━━━━━━━━━━━━━\n"
            f"🎯 নাম: <b>{p.get('name') or '-'}</b>\n"
            f"🆔 UID: <code>{p.get('uid')}</code>\n"
            f"🌍 রিজিওন: <b>{p.get('region') or '-'}</b>\n"
            f"⭐ লেভেল: <b>{p.get('level')}</b>\n"
            f"❤️ লাইক: <b>{p.get('likes')}</b>\n"
            f"🛡️ BR র‍্যাঙ্ক: <b>{p.get('br_rank') or 'Unranked'}</b>"
            + (f" ({p.get('br_points')} RP)" if p.get("br_points") else "") + "\n"
            f"⚔️ CS র‍্যাঙ্ক: <b>{p.get('cs_rank') or 'Unranked'}</b>"
            + (f" ({p.get('cs_points')}★)" if p.get("cs_points") is not None else "") + "\n")
    if g:
        card += f"🏛️ গিল্ড: <b>{g.get('name')}</b> ({g.get('members')}/{g.get('capacity')})\n"
    if p.get("pet"):
        card += f"🐾 পেট: {p['pet']['name']} (Lv {p['pet']['level']})\n"
    bot.send_message(chat_id, card)


def build_history_text(user_id) -> str:
    hist = _load_json(HISTORY_FILE)
    entries = (hist.get("users") or {}).get(str(user_id), [])
    if not entries:
        return "📜 আপনার এখনো কোনো লাইক হিস্ট্রি নেই।\n/like বা 💖 বাটন দিয়ে লাইক নিলে এখানে জমা হবে।"
    total_req = len(entries)
    total_land = sum(e.get("landed", 0) for e in entries)
    per_uid = {}
    for e in entries:
        u = per_uid.setdefault(e["uid"], {"landed": 0, "times": 0, "last": 0})
        u["landed"] += e.get("landed", 0)
        u["times"] += 1
        u["last"] = max(u["last"], e.get("ts", 0))
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=6))
    lines = ["📜 <b>আপনার লাইক হিস্ট্রি</b>",
             f"📦 মোট রিকোয়েস্ট: <b>{total_req}</b> বার",
             f"❤️ মোট ডেলিভারি: <b>{total_land}</b>টা লাইক", "", "🎯 <b>UID অনুযায়ী:</b>"]
    for uid, d in sorted(per_uid.items(), key=lambda x: -x[1]["landed"])[:12]:
        dt = datetime.fromtimestamp(d["last"], tz).strftime("%d-%m %H:%M")
        lines.append(f"• <code>{uid}</code> — {d['landed']} লাইক ({d['times']} বার, শেষ: {dt})")
    return "\n".join(lines)


def record_referral(referrer_id):
    refs = _load_json(REFS_FILE)
    cnt = refs.setdefault("counts", {})
    cnt[str(referrer_id)] = cnt.get(str(referrer_id), 0) + 1
    _save_json(REFS_FILE, refs)


def referral_count(user_id) -> int:
    return (_load_json(REFS_FILE).get("counts") or {}).get(str(user_id), 0)


# ── Admin system ──────────────────────────────────────────────
ADMINS_FILE = Path(__file__).parent / "admins.json"
_admins_cache = {"mtime": 0, "data": None}


def _load_admins() -> dict:
    try:
        mt = ADMINS_FILE.stat().st_mtime
        if _admins_cache["data"] is None or mt != _admins_cache["mtime"]:
            _admins_cache["data"] = _load_json(ADMINS_FILE)
            _admins_cache["mtime"] = mt
        return _admins_cache["data"] or {}
    except Exception:
        return {}


def _save_admins(d: dict):
    with open(ADMINS_FILE, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    _admins_cache["data"] = d
    _admins_cache["mtime"] = ADMINS_FILE.stat().st_mtime if ADMINS_FILE.exists() else 0


def is_admin(user_id) -> bool:
    try:
        uid = int(user_id)
    except Exception:
        return False
    if uid in ADMIN_IDS:
        return True
    return str(uid) in [str(a) for a in _load_admins().get("admins", [])]


def _persist_admins():
    try:
        _github_commit_file("admins.json", "update admins")
    except Exception:
        pass


@bot.message_handler(commands=["claim"])
def cmd_claim(m):
    """অ্যাডমিন দাবি: /claim <কোড>"""
    if not m.from_user:
        return
    if is_admin(m.from_user.id):
        bot.reply_to(m, "👑 আপনি এরকমই অ্যাডমিন!")
        return
    d = _load_admins()
    code = d.get("claim_code", "")
    parts = m.text.split()
    if len(parts) < 2 or parts[1] != code or not code:
        bot.reply_to(m, "⛔ ভুল কোড। কোড জানলে লিখুন: /claim <কোড>")
        return
    uid = str(m.from_user.id)
    admins = d.get("admins", [])
    if uid not in admins:
        admins.append(uid)
    d["admins"] = admins
    d["claim_code"] = ""  # একবার ব্যবহারের পর কোড নষ্ট
    _save_admins(d)
    threading.Thread(target=_persist_admins, daemon=True).start()
    bot.reply_to(m, "👑 অ্যাডমিন হিসেবে যুক্ত হলেন! এখন /pool, /guests দেখতে পাবেন।")


def reminder_loop():
    """Send daily like reminders when due."""
    while True:
        try:
            rem = _load_json(REMINDERS_FILE)
            now = int(time.time())
            changed = False
            for uid, info in list(rem.items()):
                if info.get("remind_at", 0) <= now:
                    try:
                        kb = types.InlineKeyboardMarkup()
                        kb.add(types.InlineKeyboardButton(
                            "🔥 লাইক নিন", callback_data=f"like:{info['uid']}"))
                        bot.send_message(int(uid),
                            f"🔔 <b>আজকের ফ্রি লাইক দাবি করতে ক্লিক করুন!</b>\n"
                            f"UID: <code>{info['uid']}</code>", reply_markup=kb)
                        info["remind_at"] = now + 24 * 3600
                        changed = True
                    except Exception as e:
                        print("reminder send failed:", e)
            if changed:
                _save_json(REMINDERS_FILE, rem)
        except Exception as e:
            print("reminder loop error:", e)
        time.sleep(30)


def health_loop():
    """🩺 Auto Token Health Manager: rotate-check tokens, blacklist dead ones,
    auto-re-mint the pool when healthy capacity drops."""
    idx = 0
    while True:
        try:
            pool = ff_bd_api.get_pool()
            if not pool:
                time.sleep(300)
                continue
            n = min(15, len(pool))
            chunk = pool[idx:idx + n]
            idx = (idx + n) % max(1, len(pool))
            dead_ids = []
            for t in chunk:
                tok = t.get("token")
                if not tok or t.get("exp", 0) < time.time():
                    dead_ids.append(t.get("account_id"))
                    continue
                if not ff_bd_api.check_token(tok):
                    dead_ids.append(t.get("account_id"))
            if dead_ids:
                dropped = ff_bd_api.drop_from_pool(dead_ids)
                total_dead = ff_bd_api.add_dead(dead_ids)
                print(f"[health] dropped {dropped} cached, {len(dead_ids)} dead this cycle, blacklist={total_dead}")
                threading.Thread(target=persist_usage_github, daemon=True).start()
            # অটো-রিকভারি: হেলদি পুল কমে গেলে নিজের অ্যাকাউন্ট রি-মিন্ট
            pool = ff_bd_api.get_pool()
            if len(pool) < 30:
                print("[health] pool low, forcing re-mint")
                try:
                    ff_bd_api.refresh_pool()
                except Exception as e:
                    print("[health] re-mint failed:", e)
        except Exception as e:
            print("[health] loop error:", e)
        time.sleep(1200)  # প্রতি ২০ মিনিটে এক সাইকেল


# ── Smart Spam Shield ────────────────────────────────────────
SPAM = {}        # user_id -> recent command timestamps
MUTES = {}       # user_id -> mute_until ts
VIOLATIONS = {}  # user_id -> (count, last_violation_ts)
LAST_WARN = {}   # user_id -> last mute-warning ts
LIKE_BUSY = {"until": 0, "user": None}   # global like operation window
USER_LIKE_CD = {} # user_id -> next-allowed like ts
SPAM_WINDOW = 30      # seconds
SPAM_MAX = 8          # commands per window before mute
MUTE_STEPS = (60, 300, 1800)   # escalating mute seconds
LIKE_USER_COOLDOWN = 60        # per-user seconds between /like
LIKE_GLOBAL_WINDOW = 45        # global busy window per like operation


def _spam_guard(user_id: int) -> int:
    """Returns seconds remaining if muted, else 0 (and records activity)."""
    now = time.time()
    if user_id in ADMIN_IDS:
        return 0
    mu = MUTES.get(user_id, 0)
    if now < mu:
        return int(mu - now)
    ts = SPAM.setdefault(user_id, [])
    ts.append(now)
    ts[:] = [t for t in ts if t > now - SPAM_WINDOW]
    if len(ts) > SPAM_MAX:
        cnt, last_v = VIOLATIONS.get(user_id, (0, 0))
        cnt = 1 if now - last_v > 3600 else cnt + 1
        VIOLATIONS[user_id] = (cnt, now)
        mute_s = MUTE_STEPS[min(cnt - 1, len(MUTE_STEPS) - 1)]
        MUTES[user_id] = now + mute_s
        SPAM[user_id] = []
        return mute_s
    return 0


def _spamsafe_reply(m, text: str):
    """Send a mute-warning at most once per 20s per user (no warning loops)."""
    now = time.time()
    if now - LAST_WARN.get(m.from_user.id, 0) < 20:
        return
    LAST_WARN[m.from_user.id] = now
    try:
        bot.reply_to(m, text)
    except Exception:
        pass


def _like_slot_free(user_id: int):
    """Global + per-user like cooldown. Returns (ok, seconds_to_wait)."""
    now = time.time()
    if user_id in ADMIN_IDS:
        return True, 0
    if now < LIKE_BUSY["until"]:
        return False, int(LIKE_BUSY["until"] - now)
    cd = USER_LIKE_CD.get(user_id, 0)
    if now < cd:
        return False, int(cd - now)
    LIKE_BUSY["until"] = now + LIKE_GLOBAL_WINDOW
    LIKE_BUSY["user"] = user_id
    USER_LIKE_CD[user_id] = now + LIKE_USER_COOLDOWN
    return True, 0


def wrap_with_guard(fn):
    def guarded(m, *a, **kw):
        try:
            u = m.from_user
            print(f"[userlog] id={u.id} user=@{u.username or ''} name={u.first_name or ''} cmd={m.text[:40]}")
        except Exception:
            pass
        left = _spam_guard(m.from_user.id)
        if left:
            _spamsafe_reply(m, f"⏳ অনেক দ্রুত কমান্ড দিচ্ছেন! অনুগ্রহ করে *{left} সেকেন্ড* অপেক্ষা করুন।")
            return None
        return fn(m, *a, **kw)
    return guarded


def wrap_callback_with_guard(fn):
    def guarded(c, *a, **kw):
        left = _spam_guard(c.from_user.id)
        if left:
            try:
                bot.answer_callback_query(c.id, f"⏳ {left} সেকেন্ড অপেক্ষা করুন!")
            except Exception:
                pass
            return None
        return fn(c, *a, **kw)
    return guarded


def install_guard():
    for h in bot.message_handlers:
        h["function"] = wrap_with_guard(h["function"])
    for h in bot.callback_query_handlers:
        h["function"] = wrap_callback_with_guard(h["function"])


@bot.message_handler(commands=["myid"])
def cmd_myid(m):
    left = MUTES.get(m.from_user.id, 0) - time.time()
    lines = [
        "🆔 *আপনার তথ্য*",
        f"Telegram ID: `{m.from_user.id}`",
        f"নাম: {m.from_user.first_name or '-'}",
    ]
    if m.from_user.id in ADMIN_IDS:
        lines.append("👑 স্ট্যাটাস: অ্যাডমিন (সব লিমিট মুক্ত)")
    else:
        lines.append(f"🔒 মিউট: {'হ্যাঁ, ' + str(int(left)) + ' সেকেন্ড বাকি' if left > 0 else 'নেই'}")
        cd = USER_LIKE_CD.get(m.from_user.id, 0) - time.time()
        lines.append(f"❤️ লাইক কুলডাউন: {str(int(cd)) + ' সেকেন্ড' if cd > 0 else 'প্রস্তুত'}")
    bot.reply_to(m, "\n".join(lines))


@bot.message_handler(commands=["start", "help", "menu"])
def cmd_start(m):
    # রেফার পেলোড হ্যান্ডেল: /start ref<referrer_id>
    ref_greet = ""
    parts = m.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref") and parts[1][3:].isdigit():
        try:
            record_referral(int(parts[1][3:]))
            ref_greet = "\n🎁 আপনি রেফার করে এসেছেন — স্বাগতম!"
        except Exception:
            pass
    bot.reply_to(m,
        "<b>🔥 FF BD Bot</b>\n\n"
        "নিচের বাটন থেকে এক ক্লিকে সব কাজ করুন:\n\n"
        "<b>/stats</b> <code>UID</code> — প্লেয়ার প্রোফাইল (নাম, লাইক)\n"
        "<b>/like</b> <code>UID [count]</code> — লাইক পাঠান\n"
        "<b>/profile</b> <code>UID</code> — ফুল প্লেয়ার কার্ড (লেভেল, র‍্যাঙ্ক, গিল্ড)\n"
        "<b>/setuid</b> <code>UID</code> — আপনার UID সেভ করুন (সব কমান্ডে অটো ব্যবহৃত)\n"
        "<b>/myuid</b> — সেভ করা UID দেখুন / বদলান\n"
        "<b>/stopremind</b> — রিমাইন্ডার বন্ধ\n"
        "<b>/history</b> — আপনার লাইক হিস্ট্রি\n"
        "<b>/referrals</b> — আপনার রেফার কাউন্ট\n"
        "<b>/myid</b> — আপনার ID ও কুলডাউন স্ট্যাটাস"
        + ("\n\n👑 <b>অ্যাডমিন কমান্ড</b>\n"
           "<b>/pool</b> — লাইক পুল স্ট্যাটাস\n"
           "<b>/guests</b> — গেস্ট অ্যাকাউন্ট স্ট্যাটাস"
           if is_admin(m.from_user.id if m.from_user else 0) else "")
        + ref_greet,
        reply_markup=build_menu())


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("uid:"))
def cb_uid_actions(c):
    user_id = c.from_user.id
    act = c.data.split(":", 1)[1]
    if act == "change":
        PENDING[user_id] = ("setuid", time.time())
        bot.answer_callback_query(c.id)
        bot.send_message(c.message.chat.id, "✏️ নতুন UID নম্বর পাঠান (শুধু নম্বর):")
    elif act == "clear":
        cancel_reminder(user_id)
        bot.answer_callback_query(c.id, "🗑️ UID মুছে ফেলা হয়েছে")
        try:
            bot.edit_message_text("🗑️ আপনার সেভ করা UID মুছে ফেলা হয়েছে।\n"
                                  "আবার সেট করতে: /setuid <UID>", c.message.chat.id, c.message.message_id)
        except Exception:
            bot.send_message(c.message.chat.id, "🗑️ সেভ করা UID মুছে ফেলা হয়েছে।")


@bot.message_handler(commands=["stats"])
def cmd_stats(m):
    parts = m.text.split()
    if len(parts) > 1 and parts[1].isdigit():
        uid = int(parts[1])
    else:
        saved = get_saved_uid(m.from_user.id)
        if saved:
            uid = int(saved)
        else:
            bot.reply_to(m, "⚠️ আপনার কোনো UID সেভ করা নেই!\n"
                             "<code>/setuid &lt;UID&gt;</code> লিখে আগে সেভ করুন\n"
                             "অথবা <code>/stats &lt;UID&gt;</code> ব্যবহার করুন।")
            return
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
    ok, wait = _like_slot_free(m.from_user.id)
    if not ok:
        _spamsafe_reply(m, f"⏳ লাইক সিস্টেম এখন ব্যস্ত / কুলডাউনে আছে। *{wait} সেকেন্ড* পরে আবার চেষ্টা করুন।")
        return
    parts = m.text.split()
    if len(parts) > 1 and parts[1].isdigit():
        target = int(parts[1])
    else:
        saved = get_saved_uid(m.from_user.id)
        if saved:
            target = int(saved)
        else:
            bot.reply_to(m, "⚠️ আপনার কোনো UID সেভ করা নেই!\n"
                             "<code>/setuid &lt;UID&gt;</code> লিখে আগে সেভ করুন\n"
                             "অথবা <code>/like &lt;UID&gt;</code> ব্যবহার করুন।")
            return
    try:
        pool_size = len(ff_bd_api.get_pool())
    except Exception:
        pool_size = MAX_LIKES_PER_REQUEST
    count = min(int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else pool_size,
                MAX_LIKES_PER_REQUEST)
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
    # হিস্ট্রি + অটো রিমাইন্ডার
    try:
        record_history(m.from_user.id, m.from_user.first_name or "",
                       target, count, delta if isinstance(delta, int) and delta > 0 else sent)
        if delta is None or delta > 0:
            set_reminder(m.from_user.id, m.from_user.first_name or "", target, 24 * 3600)
    except Exception as e:
        print("history/reminder record failed:", e)



@bot.message_handler(commands=["profile"])
def cmd_profile(m):
    parts = m.text.split()
    if len(parts) > 1 and parts[1].isdigit():
        target = int(parts[1])
    else:
        saved = get_saved_uid(m.from_user.id)
        if saved:
            target = int(saved)
        else:
            bot.reply_to(m, "⚠️ আপনার কোনো UID সেভ করা নেই!\n"
                             "<code>/setuid &lt;UID&gt;</code> লিখে আগে সেভ করুন\n"
                             "অথবা <code>/profile &lt;UID&gt;</code> ব্যবহার করুন।")
            return
    msg = bot.reply_to(m, "🔍 প্রোফাইল আনা হচ্ছে...")
    try:
        pool = ff_bd_api.get_pool()
        jwt = pool[0]["token"] if pool else None
        p = ff_bd_api.get_profile(target, jwt)
    except Exception as e:
        bot.edit_message_text(f"❌ এরর: {e}", msg.chat.id, msg.message_id)
        return
    if not p or p.get("error"):
        err = (p or {}).get("error", "প্লেয়ার পাওয়া যায়নি")
        bot.edit_message_text(f"❌ প্রোফাইল আনা যায়নি: {err}", msg.chat.id, msg.message_id)
        return
    import time as _t
    age = ""
    if p.get("create_at"):
        days = int((_t.time() - p["create_at"]) / 86400)
        age = f" ({days} দিন আগে)"
    region = p.get("region") or "-"
    card = (
        "👤 <b>প্লেয়ার প্রোফাইল</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎯 নাম: <b>{p.get('name') or '-'}</b>\n"
        f"🆔 UID: <code>{p.get('uid')}</code>\n"
        f"🌍 রিজিওন: <b>{region}</b>  |  🎮 {p.get('release_version') or '-'}\n"
        "━━━━━━━━━━━━━━━\n"
        f"⭐ লেভেল: <b>{p.get('level')}</b>  (EXP: {p.get('exp')})\n"
        f"❤️ লাইক: <b>{p.get('likes')}</b>\n"
        f"🛡️ BR র‍্যাঙ্ক: <b>{p.get('br_rank') or 'Unranked'}</b>"
        + (f" ({p.get('br_points')} RP)" if p.get("br_points") else "") + "\n"
        f"⚔️ CS র‍্যাঙ্ক: <b>{p.get('cs_rank') or 'Unranked'}</b>"
        + (f" ({p.get('cs_points')}★)" if p.get("cs_points") is not None else "") + "\n"
    )
    g = p.get("guild")
    if g:
        card += "━━━━━━━━━━━━━━━\n"
        card += f"🏛️ গিল্ড: <b>{g.get('name')}</b>\n"
        card += f"   Lv {g.get('level')} | 👥 {g.get('members')}/{g.get('capacity')} মেম্বার\n"
    else:
        card += "🏛️ গিল্ড: নেই\n"
    extras = []
    if p.get("pet"):
        extras.append(f"🐾 পেট: {p['pet']['name']} (Lv {p['pet']['level']})")
    if p.get("credit_score") is not None:
        extras.append(f"💯 ক্রেডিট স্কোর: {p['credit_score']}")
    if extras:
        card += "━━━━━━━━━━━━━━━\n" + "\n".join(extras) + "\n"
    if age:
        card += f"📅 অ্যাকাউন্ট বয়স{age}\n"
    bot.edit_message_text(card, msg.chat.id, msg.message_id)

@bot.message_handler(commands=["myuid"])
def cmd_myuid(m):
    txt, kb = build_uid_card(m.from_user.id)
    bot.reply_to(m, txt, reply_markup=kb)


@bot.message_handler(commands=["setuid"])
def cmd_setuid(m):
    parts = m.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(m, "⚠️ ঠিকভাবে লিখুন: <code>/setuid 18328637549</code>")
        return
    target = parts[1]
    set_reminder(m.from_user.id, m.from_user.first_name or "", target, 24 * 3600)
    threading.Thread(target=persist_usage_github, daemon=True).start()
    bot.reply_to(m, "✅ আপনার UID (<code>" + target + "</code>) সফলভাবে সেভ করা হয়েছে!\n"
                     "এখন থেকে যেকোনো কমান্ড দিলে এই UID-তেই কাজ করবে।\n\n"
                     "⏰ প্রতিদিন ২৪ ঘণ্টা পর আমি নিজে থেকেই রিমাইন্ডার পাঠাবো — "
                     "\"আজকের ফ্রি লাইক দাবি করতে ক্লিক করুন!\"\n"
                     "বন্ধ করতে: /stopremind")


@bot.message_handler(commands=["stopremind"])
def cmd_stopremind(m):
    if cancel_reminder(m.from_user.id):
        bot.reply_to(m, "✅ অটো রিমাইন্ডার বন্ধ করা হয়েছে। আবার চালু করতে /setuid দিন।")
    else:
        bot.reply_to(m, "ℹ️ আপনার কোনো রিমাইন্ডার সেট করা নেই।")


@bot.message_handler(commands=["history"])
def cmd_history(m):
    hist = _load_json(HISTORY_FILE)
    entries = (hist.get("users") or {}).get(str(m.from_user.id), [])
    if not entries:
        bot.reply_to(m, "📜 আপনার এখনো কোনো লাইক হিস্ট্রি নেই।\n"
                        "/like দিয়ে লাইক নিলে এখানে জমা হবে।")
        return
    total_req = sum(e.get("requested", 0) for e in entries)
    total_land = sum(e.get("landed", 0) for e in entries)
    per_uid = {}
    for e in entries:
        u = per_uid.setdefault(e["uid"], {"landed": 0, "times": 0, "last": 0})
        u["landed"] += e.get("landed", 0)
        u["times"] += 1
        u["last"] = max(u["last"], e.get("ts", 0))
    lines = [f"📜 <b>আপনার লাইক হিস্ট্রি</b>",
             f"📦 মোট রিকোয়েস্ট: <b>{len(entries)}</b> বার",
             f"❤️ মোট ডেলিভারি: <b>{total_land}</b>টা লাইক",
             "", "🎯 <b>UID অনুযায়ী:</b>"]
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=6))
    for uid, d in sorted(per_uid.items(), key=lambda x: -x[1]["landed"])[:12]:
        dt = datetime.fromtimestamp(d["last"], tz).strftime("%d-%m %H:%M")
        lines.append(f"• <code>{uid}</code> — {d['landed']} লাইক ({d['times']} বার, শেষ: {dt})")
    bot.reply_to(m, "\n".join(lines))


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("like:"))
def cb_like(c):
    target = c.data.split(":", 1)[1]
    if not target.isdigit():
        bot.answer_callback_query(c.id, "⚠️ অবৈধ UID")
        return
    bot.answer_callback_query(c.id, "⏳ লাইক পাঠানো হচ্ছে...")
    try:
        pool_size = len(ff_bd_api.get_pool())
    except Exception:
        pool_size = MAX_LIKES_PER_REQUEST
    usage = load_usage()
    with USAGE_LOCK:
        used = set(tuple(k) for k in usage.get("used_pairs", []))
    try:
        s0 = ff_bd_api.get_stats(int(target))
        likes_before = s0.get("likes") if s0 else None
        sent = ff_bd_api.send_like(int(target), count=pool_size, used=used)
    except Exception as e:
        bot.send_message(c.message.chat.id, f"❌ এরর: {e}")
        return
    with USAGE_LOCK:
        usage["used_pairs"] = [list(k) for k in used]
        save_usage(usage)
    threading.Thread(target=persist_usage_github, daemon=True).start()
    time.sleep(3)
    try:
        s1 = ff_bd_api.get_stats(int(target))
        likes_after = s1.get("likes") if s1 else None
    except Exception:
        likes_after = None
    delta = (likes_after - likes_before) if isinstance(likes_before, int) and isinstance(likes_after, int) else None
    try:
        record_history(c.from_user.id, c.from_user.first_name or "", int(target),
                       pool_size, delta if isinstance(delta, int) and delta > 0 else sent)
        if delta is None or delta > 0:
            set_reminder(c.from_user.id, c.from_user.first_name or "", int(target), 24 * 3600)
    except Exception:
        pass
    if delta and delta > 0:
        bot.send_message(c.message.chat.id,
            f"✅ {delta}টা লাইক যোগ হয়েছে!\n❤️ এখন মোট Likes: <b>{likes_after}</b>")
    elif delta == 0:
        bot.send_message(c.message.chat.id,
            "⚠️ আজ এই UID-তে ইতিমধ্যে লাইক দেওয়া হয়েছে — কাল আবার চেষ্টা করুন।")
    else:
        bot.send_message(c.message.chat.id,
            f"✅ {sent}টা রিকোয়েস্ট গেছে। /stats {target} দিয়ে চেক করুন।")


@bot.message_handler(func=lambda m: m.text and not m.text.startswith("/")
                             and m.text.strip().isdigit() and len(m.text.strip()) <= 12)
def cmd_plain_uid(m):
    """যখন ইউজার শুধু নম্বর পাঠায় (বাটনের পর) — সেটা UID ধরে কাজ করি।"""
    uid = m.text.strip()
    user_id = m.from_user.id
    LAST_UID[user_id] = uid
    action = PENDING.pop(user_id, None)
    saved = get_saved_uid(user_id)
    if action:
        if action[0] == "like":
            bot.reply_to(m, f"🔄 {uid} কে লাইক পাঠানো হচ্ছে...")
            run_like_flow(m.chat.id, int(uid), m.from_user)
        elif action[0] == "stats":
            run_stats_flow(m.chat.id, int(uid), m.from_user)
        elif action[0] == "profile":
            run_profile_flow(m.chat.id, int(uid), m.from_user)
        elif action[0] == "setuid":
            set_reminder(user_id, m.from_user.first_name or "", uid, 24 * 3600)
            threading.Thread(target=persist_usage_github, daemon=True).start()
            bot.reply_to(m, f"✅ আপনার UID (<code>{uid}</code>) সফলভাবে সেভ করা হয়েছে!\n"
                            "এখন থেকে যেকোনো কমান্ড দিলে এই UID-তেই কাজ করবে।",
                        reply_markup=build_menu())
        return
    if saved:
        bot.reply_to(m, f"🔄 আপনার সেভ করা UID ({saved}) কে লাইক পাঠানো হচ্ছে...\n"
                        f"অন্য UID-তে লাইক চাইলে: /like {uid}")
        run_like_flow(m.chat.id, int(saved), m.from_user)
    else:
        PENDING[user_id] = ("like", time.time())
        bot.reply_to(m, f"✅ UID পেলাম: <code>{uid}</code>\n"
                        f"কী করতে চান? নিচের বাটন থেকে বাছুন 👇",
                    reply_markup=build_menu())


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("menu:"))
def cb_menu(c):
    user_id = c.from_user.id
    act = c.data.split(":", 1)[1]
    saved = get_saved_uid(user_id)
    if act == "like":
        bot.answer_callback_query(c.id)
        target = saved or LAST_UID.get(user_id)
        if target and str(target).isdigit():
            bot.send_message(c.message.chat.id, f"🔄 {target} কে লাইক পাঠানো হচ্ছে...")
            run_like_flow(c.message.chat.id, int(target), c.from_user)
        else:
            PENDING[user_id] = ("like", time.time())
            bot.send_message(c.message.chat.id, "🎯 আপনার গেমের UID নম্বরটা পাঠান (শুধু নম্বর)")
    elif act == "stats":
        bot.answer_callback_query(c.id)
        target = saved or LAST_UID.get(user_id)
        if target and str(target).isdigit():
            run_stats_flow(c.message.chat.id, int(target), c.from_user)
        else:
            PENDING[user_id] = ("stats", time.time())
            bot.send_message(c.message.chat.id, "📊 কার স্ট্যাটাস দেখবো? UID নম্বর পাঠান")
    elif act == "profile":
        bot.answer_callback_query(c.id)
        target = saved or LAST_UID.get(user_id)
        if target and str(target).isdigit():
            run_profile_flow(c.message.chat.id, int(target), c.from_user)
        else:
            PENDING[user_id] = ("profile", time.time())
            bot.send_message(c.message.chat.id, "👤 কার প্রোফাইল দেখবো? UID নম্বর পাঠান")
    elif act == "myuid":
        bot.answer_callback_query(c.id)
        txt, kb = build_uid_card(user_id)
        bot.send_message(c.message.chat.id, txt, reply_markup=kb)
    elif act == "history":
        bot.answer_callback_query(c.id)
        bot.send_message(c.message.chat.id, build_history_text(user_id))
    elif act == "share":
        bot.answer_callback_query(c.id)
        try:
            me = bot.get_me()
            link = f"https://t.me/{me.username}?start=ref{user_id}"
            cnt = referral_count(user_id)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📤 বন্ধুকে শেয়ার করুন",
                                             switch_inline_query=f"🔥 ফ্রি ফায়ার লাইক বট! {link}"))
            bot.send_message(c.message.chat.id,
                "🔗 <b>শেয়ার ও রেফার</b>\n\n"
                f"আপনার রেফার লিংক:\n<code>{link}</code>\n\n"
                f"👥 এ পর্যন্ত রেফার: <b>{cnt}</b> জন\n\n"
                "লিংক কপি করে বন্ধুদের পাঠান — বন্ধু জয়েন করলেই কাউন্ট বাড়বে!",
                reply_markup=kb)
        except Exception as e:
            bot.send_message(c.message.chat.id, f"❌ শেয়ার লিংক বানানো গেল না: {e}")


@bot.message_handler(commands=["referrals"])
def cmd_referrals(m):
    try:
        me = bot.get_me()
        link = f"https://t.me/{me.username}?start=ref{m.from_user.id}"
    except Exception:
        link = "https://t.me/ffbdlikebot"
    bot.reply_to(m,
        f"🔗 আপনার রেফার লিংক:\n<code>{link}</code>\n\n"
        f"👥 মোট রেফার: <b>{referral_count(m.from_user.id)}</b> জন")


@bot.message_handler(commands=["guests"])
def cmd_guests(m):
    if not is_admin(m.from_user.id if m.from_user else 0):
        bot.reply_to(m, "⛔ এই কমান্ড শুধু অ্যাডমিনের জন্য।")
        return

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
    if not is_admin(m.from_user.id if m.from_user else 0):
        bot.reply_to(m, "⛔ এই কমান্ড শুধু অ্যাডমিনের জন্য।")
        return
    pool = ff_bd_api.get_pool()
    fresh = sum(1 for t in pool if t.get("exp", 0) > time.time())
    dead = ff_bd_api.dead_count() if hasattr(ff_bd_api, "dead_count") else 0
    bot.reply_to(m, f"🔋 লাইক পুল: <b>{len(pool)}</b> অ্যাকাউন্ট ({fresh} টোকেন এখনো ভ্যালিড)\n"
                    f"🩺 অটো-হেলথ: চলছে | 🖤 ব্ল্যাকলিস্টেড: {dead}টা ডেড টোকেন")


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("BOT_TOKEN env var নেই! @BotFather থেকে টোকেন নিন আর চালান:")
        print("  export BOT_TOKEN=123:ABC... && python3 bot.py")
        raise SystemExit(1)
    install_guard()
    print("bot running... [spam shield + like cooldown + token health active]")
    threading.Thread(target=reminder_loop, daemon=True).start()
    threading.Thread(target=health_loop, daemon=True).start()
    bot.infinity_polling(skip_pending=True)
