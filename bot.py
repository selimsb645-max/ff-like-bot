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
    _github_commit_file("like_ready.json", "update like readiness cache")


def _dhaka_day() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=6))).strftime("%Y-%m-%d")


def load_usage() -> dict:
    if USAGE_FILE.exists():
        try:
            u = json.loads(USAGE_FILE.read_text())
        except Exception:
            return {}
        # daily reset: the game allows one like per target per day; our pair ledger
        # must not lock a target out forever
        if u.get("pairs_day") != _dhaka_day():
            u["used_pairs"] = []
            u["pairs_day"] = _dhaka_day()
            try:
                save_usage(u)
            except Exception:
                pass
        return u
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
        types.InlineKeyboardButton("📦 History", callback_data="menu:history"),
    )
    kb.add(
        types.InlineKeyboardButton("👥 My Guild", callback_data="menu:guild"),
        types.InlineKeyboardButton("⚙️ My UID", callback_data="menu:myuid"),
    )
    kb.add(
        types.InlineKeyboardButton("🔗 Share & Refer", callback_data="menu:share"),
    )
    return kb


def build_uid_card(user_id):
    saved = get_saved_uid(user_id)
    rem = _load_json(REMINDERS_FILE).get(str(user_id), {})
    saved_at = rem.get("saved_at")
    if saved and str(saved).isdigit():
        txt = (f"⚙️ <b>My UID</b>\n\n"
               f"🆔 Saved UID: <code>{saved}</code>\n")
        if saved_at:
            import datetime
            txt += f"📅 Saved on: {datetime.datetime.fromtimestamp(saved_at).strftime('%d %b %Y, %H:%M')}\n"
        txt += "\nNow <code>/like</code> and <code>/stats</code> run on this UID automatically."
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("✏️ Change UID", callback_data="uid:change"),
               types.InlineKeyboardButton("🗑️ Delete UID", callback_data="uid:clear"))
        return txt, kb
    return ("⚠️ You have no saved UID.\n"
            "Tap the Change UID button to save one.", None)


def run_like_flow(chat_id, target, user, skip_slot=False):
    """Execute a full-pool like for target; report honestly; record history+reminder."""
    if not skip_slot:
        ok, wait = _like_slot_free(user.id)
        if not ok:
            try:
                bot.send_message(chat_id, f"⏳ Like system busy / cooling down. *{wait} seconds*, then try again.")
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
        bot.send_message(chat_id, f"❌ Error: {e}")
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
        bot.send_message(chat_id, "⚠️ Every account in the pool already liked this target. Try again tomorrow.")
    elif delta == 0:
        bot.send_message(chat_id, "⚠️ This UID was already liked today — try again tomorrow.")
    elif delta is not None and 0 < delta < sent:
        bot.send_message(chat_id, f"⚠️ {sent} attempted, actually landed {delta}.\n"
                                  f"❤️ Total Likes now: <b>{likes_after}</b>")
    elif delta is not None and delta > 0:
        bot.send_message(chat_id, f"✅ {delta} likes added!\n"
                                  f"❤️ Total Likes now: <b>{likes_after}</b>")
    else:
        bot.send_message(chat_id, f"✅ {sent} requests succeeded. Check /stats {target} to check.")


def run_stats_flow(chat_id, target, user):
    try:
        pool = ff_bd_api.get_pool()
        jwt = pool[0]["token"] if pool else None
        s = ff_bd_api.get_stats(target, jwt)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")
        return
    if not s:
        bot.send_message(chat_id, "❌ player not found।")
    elif s.get("error"):
        bot.send_message(chat_id, f"❌ {s['error']}")
    else:
        bot.send_message(chat_id,
            f"👤 <b>{s.get('name') or '-'}</b>\n🆔 UID: <code>{s.get('uid')}</code>\n"
            f"⭐ Level: <b>{s.get('level') or '-'}</b>\n❤️ Likes: <b>{s.get('likes')}</b>")


def run_profile_flow(chat_id, target, user):
    try:
        pool = ff_bd_api.get_pool()
        jwt = pool[0]["token"] if pool else None
        p = ff_bd_api.get_profile(target, jwt)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")
        return
    if not p or p.get("error"):
        bot.send_message(chat_id, "❌ Could not fetch profile.")
        return
    g = p.get("guild")
    card = ("👤 <b>Player Profile</b>\n━━━━━━━━━━━━━━━\n"
            f"🎯 Name: <b>{p.get('name') or '-'}</b>\n"
            f"🆔 UID: <code>{p.get('uid')}</code>\n"
            f"🌍 Region: <b>{p.get('region') or '-'}</b>\n"
            f"⭐ Level: <b>{p.get('level')}</b>\n"
            f"❤️ Likes: <b>{p.get('likes')}</b>\n"
            f"🛡️ BR Rank: <b>{p.get('br_rank') or 'Unranked'}</b>"
            + (f" ({p.get('br_points')} RP)" if p.get("br_points") else "") + "\n"
            f"⚔️ CS Rank: <b>{p.get('cs_rank') or 'Unranked'}</b>"
            + (f" ({p.get('cs_points')}★)" if p.get("cs_points") is not None else "") + "\n")
    if g:
        card += f"🏛️ Guild: <b>{g.get('name')}</b> ({g.get('members')}/{g.get('capacity')})\n"
    if p.get("pet"):
        card += f"🐾 Pet: {p['pet']['name']} (Lv {p['pet']['level']})\n"
    bot.send_message(chat_id, card)


def build_history_text(user_id) -> str:
    hist = _load_json(HISTORY_FILE)
    entries = (hist.get("users") or {}).get(str(user_id), [])
    if not entries:
        return "📜 No like history yet.\nIt will fill up once you use /like or the 💖 button."
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
    lines = ["📜 <b>Your Like History</b>",
             f"📦 Total requests: <b>{total_req}</b> times",
             f"❤️ Total delivered: <b>{total_land}</b> likes", "", "🎯 <b>Per UID:</b>"]
    for uid, d in sorted(per_uid.items(), key=lambda x: -x[1]["landed"])[:12]:
        dt = datetime.fromtimestamp(d["last"], tz).strftime("%d-%m %H:%M")
        lines.append(f"• <code>{uid}</code> — {d['landed']} likes ({d['times']} times, last: {dt})")
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
    """Admin claim: /claim <code>"""
    if not m.from_user:
        return
    if is_admin(m.from_user.id):
        bot.reply_to(m, "👑 You are already an admin!")
        return
    d = _load_admins()
    code = d.get("claim_code", "")
    parts = m.text.split()
    if len(parts) < 2 or parts[1] != code or not code:
        bot.reply_to(m, "⛔ Wrong code. Usage: /claim <code>")
        return
    uid = str(m.from_user.id)
    admins = d.get("admins", [])
    if uid not in admins:
        admins.append(uid)
    d["admins"] = admins
    d["claim_code"] = ""  # one-time code: cleared after use
    _save_admins(d)
    threading.Thread(target=_persist_admins, daemon=True).start()
    bot.reply_to(m, "👑 You are now an admin! You can use /pool and /guests.")


# ── Admin Dashboard / User Analytics ──────────────────────────
USERS_FILE = Path(__file__).parent / "users.json"
USERS_LOCK = threading.Lock()
BD_TZ_OFFSET = 6 * 3600  # Asia/Dhaka UTC+6


def get_all_admin_ids() -> set:
    ids = set(ADMIN_IDS)
    d = _load_json(ADMINS_FILE)
    ids.update(int(x) for x in d.get("admins", []) if str(x).isdigit())
    return ids


def update_user(user_id, **fields):
    """Sync profile fields (saved_uid, guild_id, ...) into users.json."""
    try:
        with USERS_LOCK:
            users = _load_json(USERS_FILE)
            uid = str(user_id)
            if uid in users:
                users[uid].update(fields)
                _save_json(USERS_FILE, users)
    except Exception:
        pass


def notify_admins(text: str):
    for aid in get_all_admin_ids():
        try:
            bot.send_message(aid, text)
        except Exception:
            pass


def register_user(m) -> bool:
    """Record/update user in the users DB. Returns True if BRAND NEW user."""
    try:
        u = m.from_user
        uid = str(u.id)
        with USERS_LOCK:
            users = _load_json(USERS_FILE)
            now = int(time.time())
            if uid in users:
                users[uid]["last_seen"] = now
                users[uid]["name"] = u.first_name or users[uid].get("name", "")
                users[uid]["active"] = True
                _save_json(USERS_FILE, users)
                return False
            users[uid] = {
                "name": u.first_name or "",
                "username": u.username or "",
                "joined_at": now,
                "last_seen": now,
                "active": True,
                "saved_uid": "",
                "guild_id": "",
                "points": 0,
            }
            _save_json(USERS_FILE, users)
            threading.Thread(
                target=_github_commit_file,
                args=("users.json", "update users db"),
                daemon=True,
            ).start()
            return True
    except Exception:
        return False


def _bd_date(ts: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts + BD_TZ_OFFSET, datetime.timezone.utc).strftime("%d-%m-%Y")


@bot.message_handler(commands=["admin"])
def cmd_admin(m):
    if not is_admin(m.from_user.id if m.from_user else 0):
        bot.reply_to(m, "⛔ Admin-only command.")
        return
    users = _load_json(USERS_FILE)
    if not users:
        bot.reply_to(m, "ℹ️ No users recorded yet.")
        return
    now = int(time.time())
    today0 = now - ((now + BD_TZ_OFFSET) % 86400)
    week_ago = now - 7 * 86400
    total = len(users)
    today_joined = sum(1 for u in users.values() if u.get("joined_at", 0) >= today0)
    active = sum(1 for u in users.values() if u.get("last_seen", 0) >= week_ago)
    inactive = total - active

    # like stats from history
    hist = _load_json(HISTORY_FILE)
    tot_land = tot_req = 0
    per_user = {}
    for uid, entries in hist.get("users", {}).items():
        landed = sum(e.get("landed", 0) for e in entries)
        tot_land += landed
        tot_req += len(entries)
        nm = users.get(uid, {}).get("name") or hist["users"][uid] and entries[-1].get("username") or uid
        per_user[uid] = (landed, nm)
    top = sorted(per_user.items(), key=lambda kv: -kv[1][0])[:3]

    lines = [
        "📊 *BOT USER ANALYTICS* (ADMIN PANEL)",
        "━━━━━━━━━━━━━━━━━━",
        f"👥 Total members: *{total}* users",
        f"⚡ Joined today: *{today_joined}* users",
        f"🟢 Active users (7d): *{active}* users",
        f"🔴 Inactive: *{inactive}* users",
        "━━━━━━━━━━━━━━━━━━",
        f"❤️ Total likes delivered: *{tot_land}*",
        f"🔄 Total like requests: *{tot_req}*",
    ]
    if top:
        lines.append("🏆 *Top users*:")
        for i, (uid, (landed, nm)) in enumerate(top, 1):
            lines.append(f"  {i}. {nm} — {landed} likes")
    import datetime
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("📅 Last updated: " +
                 datetime.datetime.fromtimestamp(now + BD_TZ_OFFSET, datetime.timezone.utc).strftime("%d-%m-%Y %I:%M %p"))
    bot.reply_to(m, "\n".join(lines))


@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(m):
    if not is_admin(m.from_user.id if m.from_user else 0):
        bot.reply_to(m, "⛔ Admin-only command.")
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(m, "⚠️ Usage: <code>/broadcast your message</code>")
        return
    text = parts[1].strip()
    users = _load_json(USERS_FILE)
    if not users:
        bot.reply_to(m, "ℹ️ User database is empty.")
        return
    msg = bot.reply_to(m, f"📢 Broadcast started... (total {len(users)} users)")

    def do_bc():
        ok = fail = 0
        for uid, u in users.items():
            try:
                bot.send_message(int(uid), text)
                ok += 1
                u["last_seen"] = max(u.get("last_seen", 0), 0)
            except Exception as e:
                fail += 1
                try:
                    if "Forbidden" in str(e) or "blocked" in str(e).lower():
                        u["active"] = False
                except Exception:
                    pass
            time.sleep(0.08)
        try:
            _save_json(USERS_FILE, users)
            _github_commit_file("users.json", "update users db (broadcast)")
        except Exception:
            pass
        try:
            bot.edit_message_text(f"✅ Broadcast done!\n📬 Success: {ok}\n❌ Failed: {fail}",
                                  msg.chat.id, msg.message_id)
        except Exception:
            pass

    threading.Thread(target=do_bc, daemon=True).start()


@bot.message_handler(commands=["export_users"])
def cmd_export_users(m):
    if not is_admin(m.from_user.id if m.from_user else 0):
        bot.reply_to(m, "⛔ Admin-only command.")
        return
    users = _load_json(USERS_FILE)
    if not users:
        bot.reply_to(m, "ℹ️ User database is empty.")
        return
    lines = ["user_id | name | username | joined | last_seen | active"]
    for uid, u in sorted(users.items(), key=lambda kv: kv[1].get("joined_at", 0)):
        lines.append(f"{uid} | {u.get('name','')} | @{u.get('username','')} | "
                     f"{_bd_date(u.get('joined_at',0))} | {_bd_date(u.get('last_seen',0))} | "
                     f"{'active' if u.get('active', True) else 'inactive'}")
    import io
    bio = io.BytesIO(("\n".join(lines)).encode("utf-8"))
    bio.name = "users.txt"
    bot.send_document(m.chat.id, bio, caption=f"📋 Total {len(users)} users")


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
                            "🔥 Get Likes", callback_data=f"like:{info['uid']}"))
                        bot.send_message(int(uid),
                            f"🔔 <b>Tap to claim today's free likes!</b>\n"
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
            # auto-recovery: re-mint own accounts when healthy pool runs low
            pool = ff_bd_api.get_pool()
            if len(pool) < 30:
                print("[health] pool low, forcing re-mint")
                try:
                    ff_bd_api.refresh_pool()
                except Exception as e:
                    print("[health] re-mint failed:", e)
        except Exception as e:
            print("[health] loop error:", e)
        time.sleep(1200)  # one cycle every 20 minutes


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
        try:
            m._ff_new_user = register_user(m)
            if m._ff_new_user:
                uu = m.from_user
                nm = uu.first_name or str(uu.id)
                threading.Thread(
                    target=notify_admins,
                    args=(f"🎉 New user joined!\n👤 {nm}\n🆔 {uu.id}",),
                    daemon=True,
                ).start()
        except Exception:
            pass
        left = _spam_guard(m.from_user.id)
        if left:
            _spamsafe_reply(m, f"⏳ Too many commands! Please wait *{left} seconds*.")
            return None
        return fn(m, *a, **kw)
    return guarded


def wrap_callback_with_guard(fn):
    def guarded(c, *a, **kw):
        left = _spam_guard(c.from_user.id)
        if left:
            try:
                bot.answer_callback_query(c.id, f"⏳ Wait {left} seconds!")
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
        "🆔 *Your Info*",
        f"Telegram ID: `{m.from_user.id}`",
        f"Name: {m.from_user.first_name or '-'}",
    ]
    if m.from_user.id in ADMIN_IDS:
        lines.append("👑 Status: admin (all limits free)")
    else:
        lines.append(f"🔒 Mute: {'yes, ' + str(int(left)) + ' seconds left' if left > 0 else 'none'}")
        cd = USER_LIKE_CD.get(m.from_user.id, 0) - time.time()
        lines.append(f"❤️ Like cooldown: {str(int(cd)) + ' seconds' if cd > 0 else 'ready'}")
    bot.reply_to(m, "\n".join(lines))


def build_welcome_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💖 Get Likes", callback_data="menu:like"),
        types.InlineKeyboardButton("⚙️ Set UID", callback_data="menu:setuid"),
    )
    kb.add(types.InlineKeyboardButton("📊 My Profile", callback_data="menu:profile"))
    return kb


@bot.message_handler(commands=["start", "help", "menu"])
def cmd_start(m):
    """Clean Bengali welcome: only the 7 menu buttons, no command list."""
    is_new = getattr(m, "_ff_new_user", False)
    # referral payload: /start ref<referrer_id>
    ref_greet = ""
    parts = m.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref") and parts[1][3:].isdigit():
        try:
            record_referral(int(parts[1][3:]))
            ref_greet = "\n🎁 রেফারেলের মাধ্যমে জয়েন করেছেন — ধন্যবাদ!"
        except Exception:
            pass

    welcome = (
        "🔥 <b>স্বাগতম!</b> FF • FREE LIKE BOT-এ আপনাকে স্বাগতম। 🎮\n\n"
        "এখানে আপনি পাবেন এক ক্লিকে লাইক বৃদ্ধি, প্লেয়ার প্রোফাইল চেক, "
        "গিল্ড গ্লোরি ও ইউআইডি ম্যানেজমেন্টের সুব্যবস্থা। "
        "আপনার ফ্রি ফায়ার অভিজ্ঞতাকে করুন আরও দারুণ! 🚀\n\n"
        "নিচের বাটনগুলোতে ক্লিক করে আপনার প্রয়োজনীয় ফিচারটি ব্যবহার করুন 👇"
    )

    if is_new:
        u = m.from_user
        info = (
            f"\n\n━━━━━━━━━━━━━━━\n"
            f"🎉 আপনার অ্যাকাউন্ট তৈরি হয়েছে!\n"
            f"🆔 আইডি: <code>{u.id}</code>\n"
            f"📅 রেজিস্ট্রেশন: {_bd_date(time.time())}\n"
            f"❤️ ফ্রি লাইক: <b>আনলিমিটেড</b>"
        )
        bot.reply_to(m, info.lstrip("\n") + "\n\n" + welcome + ref_greet,
                     reply_markup=build_menu())
        return

    bot.reply_to(m, welcome + ref_greet, reply_markup=build_menu())


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("uid:"))
def cb_uid_actions(c):
    user_id = c.from_user.id
    act = c.data.split(":", 1)[1]
    if act == "change":
        PENDING[user_id] = ("setuid", time.time())
        bot.answer_callback_query(c.id)
        bot.send_message(c.message.chat.id, "✏️ Send your new UID (numbers only):")
    elif act == "clear":
        cancel_reminder(user_id)
        update_user(user_id, saved_uid="")
        bot.answer_callback_query(c.id, "🗑️ UID deleted")
        try:
            bot.edit_message_text("🗑️ Your saved UID was deleted.\n"
                                  "Set again: /setuid <UID>", c.message.chat.id, c.message.message_id)
        except Exception:
            bot.send_message(c.message.chat.id, "🗑️ Saved UID deleted.")


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
            bot.reply_to(m, "⚠️ No saved UID!\n"
                             "First save it: <code>/setuid &lt;UID&gt;</code>\n"
                             "or use <code>/stats &lt;UID&gt;</code>.")
            return
    msg = bot.reply_to(m, "🔄 Searching...")
    try:
        s = ff_bd_api.get_stats(uid)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", msg.chat.id, msg.message_id)
        return
    if not s or s.get("error"):
        bot.edit_message_text(f"❌ {s.get('error') if s else 'error'}\nIs the UID correct? A BD-server player?",
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
        _spamsafe_reply(m, f"⏳ Like system is busy / cooling down. *{wait} seconds*, then try again.")
        return
    parts = m.text.split()
    if len(parts) > 1 and parts[1].isdigit():
        target = int(parts[1])
    else:
        saved = get_saved_uid(m.from_user.id)
        if saved:
            target = int(saved)
        else:
            bot.reply_to(m, "⚠️ No saved UID!\n"
                             "First save it: <code>/setuid &lt;UID&gt;</code>\n"
                             "or use <code>/like &lt;UID&gt;</code>.")
            return
    try:
        pool_size = len(ff_bd_api.get_pool())
    except Exception:
        pool_size = MAX_LIKES_PER_REQUEST
    count = min(int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else pool_size,
                MAX_LIKES_PER_REQUEST)
    msg = bot.reply_to(m, f"🔄 {target} → {count} likes being sent...")
    usage = load_usage()
    with USAGE_LOCK:
        used = set(tuple(k) for k in usage.get("used_pairs", []))
    # like count before
    try:
        s0 = ff_bd_api.get_stats(target)
        likes_before = s0.get("likes") if s0 else None
    except Exception:
        likes_before = None
    try:
        sent = ff_bd_api.send_like(target, count=count, used=used)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", msg.chat.id, msg.message_id)
        return
    with USAGE_LOCK:
        usage["used_pairs"] = [list(k) for k in used]
        save_usage(usage)
    threading.Thread(target=persist_usage_github, daemon=True).start()
    # like count after
    time.sleep(3)
    try:
        s1 = ff_bd_api.get_stats(target)
        likes_after = s1.get("likes") if s1 else None
    except Exception:
        likes_after = None

    if sent == 0:
        bot.edit_message_text("⚠️ Every account in the pool already liked this target. Try again later.",
                              msg.chat.id, msg.message_id)
        return
    delta = None
    if isinstance(likes_before, int) and isinstance(likes_after, int):
        delta = likes_after - likes_before
    if delta == 0:
        text = ("❌ Like did not land — server count did not increase "
                f"(before {likes_before}, still {likes_after})।\n"
                "Common reason: pool accounts already liked this profile; "
                "one account can like a profile only once.")
    elif delta is not None and delta < sent:
        text = (f"⚠️ {sent} attempted, actually landed {delta}.\n"
                f"❤️ Total Likes now: <b>{likes_after}</b>")
    elif delta is not None:
        text = (f"✅ {delta} likes added!\n"
                f"❤️ Total Likes now: <b>{likes_after}</b>")
    else:
        text = (f"✅ {sent} requests succeeded, but verification failed.\n"
                f"/stats {target} to check yourself.")
    bot.edit_message_text(text, msg.chat.id, msg.message_id)
    # history + auto reminder
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
            bot.reply_to(m, "⚠️ No saved UID!\n"
                             "First save it: <code>/setuid &lt;UID&gt;</code>\n"
                             "or use <code>/profile &lt;UID&gt;</code>.")
            return
    msg = bot.reply_to(m, "🔍 Fetching profile...")
    try:
        pool = ff_bd_api.get_pool()
        jwt = pool[0]["token"] if pool else None
        p = ff_bd_api.get_profile(target, jwt)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", msg.chat.id, msg.message_id)
        return
    if not p or p.get("error"):
        err = (p or {}).get("error", "player not found")
        bot.edit_message_text(f"❌ Could not fetch profile: {err}", msg.chat.id, msg.message_id)
        return
    import time as _t
    age = ""
    if p.get("create_at"):
        days = int((_t.time() - p["create_at"]) / 86400)
        age = f" ({days} days ago)"
    region = p.get("region") or "-"
    card = (
        "👤 <b>Player Profile</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎯 Name: <b>{p.get('name') or '-'}</b>\n"
        f"🆔 UID: <code>{p.get('uid')}</code>\n"
        f"🌍 Region: <b>{region}</b>  |  🎮 {p.get('release_version') or '-'}\n"
        "━━━━━━━━━━━━━━━\n"
        f"⭐ Level: <b>{p.get('level')}</b>  (EXP: {p.get('exp')})\n"
        f"❤️ Likes: <b>{p.get('likes')}</b>\n"
        f"🛡️ BR Rank: <b>{p.get('br_rank') or 'Unranked'}</b>"
        + (f" ({p.get('br_points')} RP)" if p.get("br_points") else "") + "\n"
        f"⚔️ CS Rank: <b>{p.get('cs_rank') or 'Unranked'}</b>"
        + (f" ({p.get('cs_points')}★)" if p.get("cs_points") is not None else "") + "\n"
    )
    g = p.get("guild")
    if g:
        card += "━━━━━━━━━━━━━━━\n"
        card += f"🏛️ Guild: <b>{g.get('name')}</b>\n"
        card += f"   Lv {g.get('level')} | 👥 {g.get('members')}/{g.get('capacity')} members\n"
    else:
        card += "🏛️ Guild: none\n"
    extras = []
    if p.get("pet"):
        extras.append(f"🐾 Pet: {p['pet']['name']} (Lv {p['pet']['level']})")
    if p.get("credit_score") is not None:
        extras.append(f"💯 Credit score: {p['credit_score']}")
    if extras:
        card += "━━━━━━━━━━━━━━━\n" + "\n".join(extras) + "\n"
    if age:
        card += f"📅 Account age{age}\n"
    bot.edit_message_text(card, msg.chat.id, msg.message_id)

@bot.message_handler(commands=["recover"])
def cmd_recover(m):
    """Admin-only ID recovery: full identity card for any UID (name, level, ranks,
    guild, creation date, last login, pet, credit score, signature)."""
    if not is_admin(m.from_user.id if m.from_user else 0):
        bot.reply_to(m, "⛔ Admin-only command.")
        return
    parts = m.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(m, "⚠️ Correct format: <code>/recover 18328637549</code>")
        return
    uid = int(parts[1])
    msg = bot.reply_to(m, "🔄 আইডি খোঁজা হচ্ছে...")
    try:
        pool = ff_bd_api.get_pool()
        jwt = pool[0]["token"] if pool else None
        p = ff_bd_api.get_profile(uid, jwt)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", msg.chat.id, msg.message_id)
        return
    if not p or p.get("error"):
        bot.edit_message_text(
            "❌ এই UID দিয়ে কোনো অ্যাকাউন্ট পাওয়া যায়নি।\n"
            "UID ঠিক কিনা যাচাই করুন — অ্যাকাউন্টটি বাতিল (banned) বা ডিলিট হয়ে থাকতে পারে।",
            msg.chat.id, msg.message_id)
        return
    import time as _t
    created = ""
    if p.get("create_at"):
        try:
            c = _t.strftime("%d %b %Y", _t.gmtime(p["create_at"]))
            days = int((_t.time() - p["create_at"]) / 86400)
            created = f"{c} ({days} দিন আগে)"
        except Exception:
            created = str(p.get("create_at"))
    last = ""
    if p.get("last_login"):
        try:
            ll = int(p["last_login"])
            if ll > 1e9:
                days_ago = int((_t.time() - ll) / 86400)
                base = _t.strftime("%d %b %Y", _t.gmtime(ll))
                last = base + (" (আজ)" if days_ago <= 0 else f" ({days_ago} দিন আগে)")
            else:
                last = str(ll)
        except Exception:
            last = str(p.get("last_login"))
    g = p.get("guild")
    card = (
        "🔎 <b>আইডি রিকভারি রিপোর্ট</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎯 বর্তমান নাম: <b>{p.get('name') or '-'}</b>\n"
        f"🆔 UID: <code>{p.get('uid')}</code>\n"
        f"🌍 রিজিয়ন: <b>{p.get('region') or '-'}</b>  |  🎮 {p.get('release_version') or '-'}\n"
        "━━━━━━━━━━━━━━━\n"
        f"⭐ লেভেল: <b>{p.get('level')}</b>  (EXP: {p.get('exp')})\n"
        f"❤️ লাইক: <b>{p.get('likes')}</b>\n"
        f"🛡️ BR র‍্যাঙ্ক: <b>{p.get('br_rank') or 'Unranked'}</b>"
        + (f" ({p.get('br_points')} RP)" if p.get('br_points') else "") + "\n"
        f"⚔️ CS র‍্যাঙ্ক: <b>{p.get('cs_rank') or 'Unranked'}</b>"
        + (f" ({p.get('cs_points')}★)" if p.get('cs_points') is not None else "") + "\n"
        "━━━━━━━━━━━━━━━\n"
    )
    if g:
        card += f"🏛️ গিল্ড: <b>{g.get('name')}</b> (ID: <code>{g.get('id')}</code>)\n"
        card += f"   Lv {g.get('level')} | 👥 {g.get('members')}/{g.get('capacity')} জন\n"
    else:
        card += "🏛️ গিল্ড: নেই\n"
    if created:
        card += f"📅 তৈরি: <b>{created}</b>\n"
    if last:
        card += f"🕒 শেষ লগইন: <b>{last}</b>\n"
    extras = []
    if p.get("pet"):
        extras.append(f"🐾 পেট: {p['pet']['name']} (Lv {p['pet']['level']})")
    if p.get("credit_score") is not None:
        extras.append(f"💯 ক্রেডিট স্কোর: {p['credit_score']}")
    if p.get("signature"):
        extras.append(f"✍️ সিগনেচার: {p['signature']}")
    if extras:
        card += "━━━━━━━━━━━━━━━\n" + "\n".join(extras) + "\n"
    card += "━━━━━━━━━━━━━━━\n✅ স্ট্যাটাস: অ্যাকাউন্ট সক্রিয়"
    # hacked-account recovery guide (official Garena process)
    card += (
        "\n📋 <b>রিকভারি প্রমাণ (Garena support-এ দিন):</b>\n"
        f"UID: <code>{p.get('uid')}</code> | তৈরি: {created or '-'} | শেষ লগইন: {last or '-'} | বর্তমান নাম: {p.get('name')}\n"
        "━━━━━━━━━━━━━━━\n"
        "🔒 <b>হ্যাক হলে অফিসিয়াল ধাপ:</b>\n"
        "1. গেমে: Settings → Basic → Customer Service (বা ff.support.garena.com)\n"
        "2. 'Hacked Account / Account Recovery' ফর্ম পূরণ করুন\n"
        "3. দিন: UID + তৈরির তারিখ + হ্যাকের আগের নাম + রিচার্জ রসিদ/ট্রানজেকশন আইডি\n"
        "4. যত দ্রুত সম্ভব রিপোর্ট করুন — বাইন্ডিং বদলে গেলে রিকভারি কঠিন হয়"
    )
    bot.edit_message_text(card, msg.chat.id, msg.message_id)


@bot.message_handler(commands=["myuid"])
def cmd_myuid(m):
    txt, kb = build_uid_card(m.from_user.id)
    bot.reply_to(m, txt, reply_markup=kb)


@bot.message_handler(commands=["setuid"])
def cmd_setuid(m):
    parts = m.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(m, "⚠️ Correct format: <code>/setuid 18328637549</code>")
        return
    target = parts[1]
    set_reminder(m.from_user.id, m.from_user.first_name or "", target, 24 * 3600)
    update_user(m.from_user.id, saved_uid=target)
    threading.Thread(target=persist_usage_github, daemon=True).start()
    bot.reply_to(m, "✅ Your UID (<code>" + target + "</code>) saved successfully!\n"
                     "Every command will now run on this UID.\n\n"
                     "⏰ I will send a daily reminder every 24h — "
                     "\"Claim your daily free likes!\"\n"
                     "Disable: /stopremind")


@bot.message_handler(commands=["stopremind"])
def cmd_stopremind(m):
    if cancel_reminder(m.from_user.id):
        bot.reply_to(m, "✅ Auto-reminder disabled. Use /setuid to enable again.")
    else:
        bot.reply_to(m, "ℹ️ You have no reminder set.")


@bot.message_handler(commands=["history"])
def cmd_history(m):
    hist = _load_json(HISTORY_FILE)
    entries = (hist.get("users") or {}).get(str(m.from_user.id), [])
    if not entries:
        bot.reply_to(m, "📜 No like history yet.\n"
                        "It will fill up once you use /like.")
        return
    total_req = sum(e.get("requested", 0) for e in entries)
    total_land = sum(e.get("landed", 0) for e in entries)
    per_uid = {}
    for e in entries:
        u = per_uid.setdefault(e["uid"], {"landed": 0, "times": 0, "last": 0})
        u["landed"] += e.get("landed", 0)
        u["times"] += 1
        u["last"] = max(u["last"], e.get("ts", 0))
    lines = [f"📜 <b>Your Like History</b>",
             f"📦 Total requests: <b>{len(entries)}</b> times",
             f"❤️ Total delivered: <b>{total_land}</b> likes",
             "", "🎯 <b>Per UID:</b>"]
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=6))
    for uid, d in sorted(per_uid.items(), key=lambda x: -x[1]["landed"])[:12]:
        dt = datetime.fromtimestamp(d["last"], tz).strftime("%d-%m %H:%M")
        lines.append(f"• <code>{uid}</code> — {d['landed']} likes ({d['times']} times, last: {dt})")
    bot.reply_to(m, "\n".join(lines))


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("like:"))
def cb_like(c):
    target = c.data.split(":", 1)[1]
    if not target.isdigit():
        bot.answer_callback_query(c.id, "⚠️ Invalid UID")
        return
    bot.answer_callback_query(c.id, "⏳ sending likes...")
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
        bot.send_message(c.message.chat.id, f"❌ Error: {e}")
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
            f"✅ {delta} likes added!\n❤️ Total Likes now: <b>{likes_after}</b>")
    elif delta == 0:
        bot.send_message(c.message.chat.id,
            "⚠️ This UID was already liked today — try again tomorrow.")
    else:
        bot.send_message(c.message.chat.id,
            f"✅ {sent} requests sent. Check /stats {target} to check.")


@bot.message_handler(func=lambda m: m.text and not m.text.startswith("/")
                             and m.text.strip().isdigit() and len(m.text.strip()) <= 12)
def cmd_plain_uid(m):
    """User sent only a number (after a button) — treat it as UID."""
    uid = m.text.strip()
    user_id = m.from_user.id
    LAST_UID[user_id] = uid
    action = PENDING.pop(user_id, None)
    saved = get_saved_uid(user_id)
    if action:
        if action[0] == "like":
            bot.reply_to(m, f"🔄 {uid} → sending likes...")
            run_like_flow(m.chat.id, int(uid), m.from_user)
        elif action[0] == "stats":
            run_stats_flow(m.chat.id, int(uid), m.from_user)
        elif action[0] == "profile":
            run_profile_flow(m.chat.id, int(uid), m.from_user)
        elif action[0] == "setguild":
            rem = _load_json(REMINDERS_FILE)
            e = rem.get(str(user_id)) or {}
            e.update({"uid": e.get("uid", uid), "guild_id": uid,
                      "username": m.from_user.first_name or e.get("username", ""),
                      "saved_at": int(time.time())})
            if "remind_at" not in e:
                e["remind_at"] = int(time.time() + 24 * 3600)
            rem[str(user_id)] = e
            _save_json(REMINDERS_FILE, rem)
            threading.Thread(target=persist_usage_github, daemon=True).start()
            bot.reply_to(m, f"✅ Guild ID (<code>{uid}</code>) saved!\n"
                            "The [🏰 My Guild] button will now like your guild.")
        elif action[0] == "setuid":
            set_reminder(user_id, m.from_user.first_name or "", uid, 24 * 3600)
            update_user(user_id, saved_uid=uid)
            threading.Thread(target=persist_usage_github, daemon=True).start()
            bot.reply_to(m, f"✅ Your UID (<code>{uid}</code>) saved successfully!\n"
                            "Every command will now run on this UID.",
                        reply_markup=build_menu())
        return
    if saved:
        bot.reply_to(m, f"🔄 Sending likes to your saved UID ({saved})...\n"
                        f"For a different UID: /like {uid}")
        run_like_flow(m.chat.id, int(saved), m.from_user)
    else:
        PENDING[user_id] = ("like", time.time())
        bot.reply_to(m, f"✅ Got it — UID: <code>{uid}</code>\n"
                        f"Pick what you want from the buttons below 👇",
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
            bot.send_message(c.message.chat.id, f"🔄 {target} → sending likes...")
            run_like_flow(c.message.chat.id, int(target), c.from_user)
        else:
            PENDING[user_id] = ("like", time.time())
            bot.send_message(c.message.chat.id, "🎯 Send your game UID (numbers only)")
    elif act == "stats":
        bot.answer_callback_query(c.id)
        target = saved or LAST_UID.get(user_id)
        if target and str(target).isdigit():
            run_stats_flow(c.message.chat.id, int(target), c.from_user)
        else:
            PENDING[user_id] = ("stats", time.time())
            bot.send_message(c.message.chat.id, "📊 Whose stats? Send a UID")
    elif act == "profile":
        bot.answer_callback_query(c.id)
        target = saved or LAST_UID.get(user_id)
        if target and str(target).isdigit():
            run_profile_flow(c.message.chat.id, int(target), c.from_user)
        else:
            PENDING[user_id] = ("profile", time.time())
            bot.send_message(c.message.chat.id, "👤 Whose profile? Send a UID")
    elif act == "guild":
        bot.answer_callback_query(c.id)
        clan = get_saved_guild(user_id)
        if clan and str(clan).isdigit():
            if _like_slot_free(user_id)[0]:
                bot.send_message(c.message.chat.id, f"🛡️ Sending likes to members of Guild {clan}...")
                threading.Thread(target=run_guildlike_flow,
                                 args=(c.message.chat.id, int(clan), c.from_user, 20), daemon=True).start()
            else:
                bot.send_message(c.message.chat.id, "⏳ Like system busy, try again in a bit.")
        else:
            PENDING[user_id] = ("setguild", time.time())
            bot.send_message(c.message.chat.id, "🏰 Send your Guild ID (Clan ID)")
    elif act == "setuid":
        bot.answer_callback_query(c.id)
        PENDING[user_id] = ("setuid", time.time())
        bot.send_message(c.message.chat.id,
            "⚙️ Send your Free Fire UID (numbers only).\n"
            "The UID is shown on your in-game profile.")
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
            kb.add(types.InlineKeyboardButton("📤 Share with friends",
                                             switch_inline_query=f"🔥 Free Fire Like Bot! {link}"))
            bot.send_message(c.message.chat.id,
                "🔗 <b>Share & Refer</b>\n\n"
                f"Your referral link:\n<code>{link}</code>\n\n"
                f"👥 Referrals so far: <b>{cnt}</b>\n\n"
                "Copy the link and send it to friends — the count grows with each join!",
                reply_markup=kb)
        except Exception as e:
            bot.send_message(c.message.chat.id, f"❌ Could not create a share link: {e}")


@bot.message_handler(commands=["referrals"])
def cmd_referrals(m):
    try:
        me = bot.get_me()
        link = f"https://t.me/{me.username}?start=ref{m.from_user.id}"
    except Exception:
        link = "https://t.me/ffbdlikebot"
    bot.reply_to(m,
        f"🔗 Your referral link:\n<code>{link}</code>\n\n"
        f"👥 Total referrals: <b>{referral_count(m.from_user.id)}</b> users")


# ── Guild Bulk Like ─────────────────────────────────────────
def get_saved_guild(user_id) -> str | None:
    rem = _load_json(REMINDERS_FILE)
    e = rem.get(str(user_id)) or {}
    return e.get("guild_id")


def run_guildlike_flow(chat_id, clan_id, user, count=20):
    """Guild bulk like in background thread with live progress updates."""
    try:
        g = ff_bd_api.get_guild(clan_id)
    except Exception:
        g = None
    if not g or not g.get("uids"):
        bot.send_message(chat_id, f"❌ Guild {clan_id} not found. Is the Guild ID correct? A BD-server guild?")
        return
    uids = [u for u in g["uids"] if isinstance(u, int) and u > 0]
    total = len(uids)
    prog = bot.send_message(chat_id,
        f"🛡️ <b>Guild Bulk Like</b>\n"
        f"🏰 Guild: <b>{g.get('name', clan_id)}</b>\n"
        f"👥 Active members: <b>{total}</b> (total {g.get('members_total', '?')} users)\n"
        f"⏳ starting...")
    msg_id, chat = prog.message_id, prog.chat.id
    sent_total, ok_members = 0, 0
    used = set(tuple(k) for k in load_usage().get("used_pairs", []))
    for i, uid in enumerate(uids, 1):
        try:
            sent = ff_bd_api.send_like(uid, count=count, used=used)
        except Exception:
            sent = 0
        sent_total += sent
        if sent:
            ok_members += 1
        blocks = "▰" * i + "▱" * (total - i)
        pct = int(i * 100 / total)
        try:
            if i == total or i % 2 == 1:
                bot.edit_message_text(
                    f"🛡️ <b>Guild Bulk Like</b>\n"
                    f"🏰 Guild: <b>{g.get('name', clan_id)}</b>\n"
                    f"⏳ Progress: [{blocks}] {pct}% ({i}/{total})\n"
                    f"✅ Likes sent: {sent_total} | 👤 Members covered: {ok_members}",
                    chat, msg_id)
        except Exception:
            pass
        time.sleep(1.5)
    # persist usage
    try:
        usage = load_usage()
        with USAGE_LOCK:
            usage["used_pairs"] = [list(k) for k in used]
            save_usage(usage)
        threading.Thread(target=persist_usage_github, daemon=True).start()
    except Exception:
        pass
    bot.edit_message_text(
        f"🛡️ <b>Guild Bulk Like — Final Report</b>\n"
        f"🏰 Guild: <b>{g.get('name', clan_id)}</b> (Lv {g.get('level', '?')})\n"
        f"👥 Active members: {total} (total {g.get('members_total', '?')} users)\n"
        f"✅ Successfully sent: <b>{sent_total}</b> likes\n"
        f"👤 Members who received likes: <b>{ok_members}</b>\n"
        f"⚠️ Skipped (already liked / dead): {total - ok_members}",
        chat, msg_id)
    # history: per-member accounting
    for uid in uids:
        record_history(user.id, user.first_name or "", uid, count, 0)
    persist_usage_github()


@bot.message_handler(commands=["guildlike"])
def cmd_guildlike(m):
    if not _like_slot_free(m.from_user.id)[0]:
        _spamsafe_reply(m, "⏳ Like system busy / cooling down. Try again in a bit.")
        return
    parts = m.text.split()
    clan = parts[1] if len(parts) > 1 and parts[1].isdigit() else get_saved_guild(m.from_user.id) or get_saved_uid(m.from_user.id)
    if not clan or not str(clan).isdigit():
        bot.reply_to(m, "⚠️ Correct format: <code>/guildlike 3071565979</code>\n"
                        "or first save it with /setguild <GUILD_ID>.")
        return
    try:
        count = min(int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 20, 100)
    except Exception:
        count = 20
    threading.Thread(target=run_guildlike_flow,
                     args=(m.chat.id, int(clan), m.from_user, count), daemon=True).start()


@bot.message_handler(commands=["setguild"])
def cmd_setguild(m):
    parts = m.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(m, "⚠️ Correct format: <code>/setguild 3071565979</code>")
        return
    clan = parts[1]
    rem = _load_json(REMINDERS_FILE)
    e = rem.get(str(m.from_user.id)) or {}
    e.update({"uid": e.get("uid", clan), "guild_id": clan,
              "username": m.from_user.first_name or e.get("username", ""),
              "saved_at": int(time.time())})
    update_user(m.from_user.id, guild_id=clan)
    if "remind_at" not in e:
        e["remind_at"] = int(time.time() + 24 * 3600)
    rem[str(m.from_user.id)] = e
    _save_json(REMINDERS_FILE, rem)
    threading.Thread(target=persist_usage_github, daemon=True).start()
    bot.reply_to(m, f"✅ Your Guild ID (<code>{clan}</code>) saved!\n"
                    "The [🏰 My Guild] button now likes all active members of your guild.")


@bot.message_handler(commands=["guests"])
def cmd_guests(m):
    if not is_admin(m.from_user.id if m.from_user else 0):
        bot.reply_to(m, "⛔ Admin-only command.")
        return

    msg = bot.reply_to(m, "🔄 Fetching account status...")
    import base64
    def gh_json(path):
        try:
            r = httpx.get(
                f"https://api.github.com/repos/selimsb645-max/ff-like-bot/contents/{path}",
                headers={"Accept": "application/vnd.github+json"}, timeout=20)
            r.raise_for_status()
            return json.loads(base64.b64decode(r.json()["content"]))
        except Exception:
            return []
    data = gh_json("guests_manager/guests.json")
    regs = gh_json("guests_manager/registered_pool.json")
    phone = gh_json("phone_accounts.json")
    prog = gh_json("level_progress.json")
    prog = prog if isinstance(prog, dict) else {}
    # ACTIVATED = engine played real matches on it (cycles >= 1) -> game profile live
    phone_active = sum(
        1 for r in phone
        if ((prog.get(str(r.get("uid"))) or {}).get("cycles") or 0) >= 1)
    phone_inactive = len(phone) - phone_active
    n = len(data)
    reg_bd = sum(1 for r in regs if str(r.get("region", "")).upper() == "BD")
    reg_other = len(regs) - reg_bd
    total = n + len(regs)
    last_uid = regs[-1].get("uid", "?") if regs else (data[-1].get("uid", "?") if data else "?")
    prog = reg_bd  # usable BD accounts = real progress toward the 1000 goal
    bar_len = min(prog * 10 // 1000, 10)
    bar = "▰" * bar_len + "▱" * (10 - bar_len)
    st = gh_json("guests_manager/register_status.json")
    smap = {"no_bd_proxy": "❌ No BD proxy found — hunting again next run",
            "running": "🔄 BD registration running",
            "done": "✅ Target complete",
            "partial": "⏸ Run ended — resumes next run"}
    st_line = ""  # cloud register paused; phone pipeline is primary
    bot.edit_message_text(
        f"👥 Account Status (target <b>1000</b>)\n"
        f"✅ Registered BD (usable): <b>{reg_bd}</b>\n"
        f"{bar} <b>{min(prog * 100 // 1000, 100)}%</b>\n"
        f"🌎 Registered other regions: {reg_other}\n"
        f"📦 Guest stockpile: <b>{n}</b> (US-born — not usable on BD)\n"
        f"📱 Phone BD (Termux): <b>{len(phone)}</b>\n"
        f"✅ Active (game profile live): <b>{phone_active}</b>\n"
        f"⏳ Inactive (activation pending): <b>{phone_inactive}</b>\n"
        f"🧾 Total inventory: {total}\n"
        f"🔔 Latest UID: <code>{last_uid}</code>{st_line}",
        msg.chat.id, msg.message_id)


@bot.message_handler(commands=["pool"])
def cmd_pool(m):
    if not is_admin(m.from_user.id if m.from_user else 0):
        bot.reply_to(m, "⛔ Admin-only command.")
        return
    pool = ff_bd_api.get_pool()
    fresh = sum(1 for t in pool if t.get("exp", 0) > time.time())
    dead = ff_bd_api.dead_count() if hasattr(ff_bd_api, "dead_count") else 0
    stock = reg_bd = reg_other = 0
    try:
        stock = len(json.loads(open("guests_manager/guests.json").read()))
    except Exception:
        pass
    try:
        regs = json.loads(open("guests_manager/registered_pool.json").read())
        reg_bd = sum(1 for r in regs if str(r.get("region", "")).upper() == "BD")
        reg_other = len(regs) - reg_bd
    except Exception:
        pass
    try:
        phone = json.loads(open("phone_accounts.json").read())
        rc = ff_bd_api.ready_uids()
        pr = sum(1 for r in phone if (rc.get(str(r.get("uid"))) or {}).get("ready"))
        pool_line2 = f"📱 Termux BD: <b>{len(phone)}</b> (like-ready: {pr})\n"
    except Exception:
        pool_line2 = ""
    bot.reply_to(m, f"🔋 Like pool: <b>{len(pool)}</b> accounts ({fresh} tokens still valid)\n"
                    f"🩺 Auto-health: running | 🖤 Blacklisted: {dead} dead tokens\n"
                    f"✅ Registered BD: <b>{reg_bd}</b> | Other regions: {reg_other}\n"
                    f"📦 Guest stockpile: <b>{stock}</b> (US-born — not usable on BD)\n"
                    f"{pool_line2}")


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("BOT_TOKEN env var missing! Get a token from @BotFather and set it:")
        print("  export BOT_TOKEN=123:ABC... && python3 bot.py")
        raise SystemExit(1)
    install_guard()
    print("bot running... [spam shield + like cooldown + token health active]")
    threading.Thread(target=reminder_loop, daemon=True).start()
    threading.Thread(target=health_loop, daemon=True).start()
    bot.infinity_polling(skip_pending=True)
