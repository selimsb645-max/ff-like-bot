#!/usr/bin/env python3
"""
FF BD Telegram Bot — stats + like automation.
Setup: get a token from @BotFather, then:
  export BOT_TOKEN=123456:ABC...
  python3 bot.py
"""
import os, json, time, threading, tempfile, random, re
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
        types.InlineKeyboardButton("🏰 My Guild", callback_data="menu:guild"),
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



# ============ NEW: SHAREABLE STAT CARD (/card) ============
CARD_DIR = os.path.join(tempfile.gettempdir(), "ffcards")
os.makedirs(CARD_DIR, exist_ok=True)


def _card_font(size):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/system/fonts/Roboto-Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def make_stat_card(uid, data):
    """Render a premium PNG stat card. Returns path or None (bot falls back to text)."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    try:
        W, H = 900, 420
        img = Image.new("RGB", (W, H), (10, 14, 24))
        d = ImageDraw.Draw(img)
        # gradient background
        for y in range(H):
            r = int(10 + 18 * (y / H))
            g = int(14 + 26 * (y / H))
            b = int(24 + 52 * (y / H))
            d.line([(0, y), (W, y)], fill=(r, g, b))
        # accent bars
        d.rectangle([0, 0, W, 6], fill=(255, 122, 0))
        d.rectangle([0, H - 6, W, H], fill=(0, 200, 255))
        # panel
        d.rounded_rectangle([40, 46, W - 40, H - 46], radius=26, fill=(17, 23, 38),
                            outline=(38, 48, 72), width=2)
        name = str(data.get("name") or "Player")[:18]
        d.text((78, 84), name, font=_card_font(46), fill=(255, 255, 255))
        d.text((80, 148), "FREE FIRE  ·  BD SERVER", font=_card_font(19), fill=(255, 150, 60))
        # stat boxes
        stats = [
            ("UID", uid),
            ("LEVEL", data.get("level") if data.get("level") is not None else "-"),
            ("LIKES", data.get("likes") if data.get("likes") is not None else "-"),
            ("EXP", data.get("exp") if data.get("exp") is not None else "-"),
        ]
        x = 78
        for label, val in stats:
            d.rounded_rectangle([x, 200, x + 185, 300], radius=16, fill=(23, 31, 50),
                                outline=(45, 58, 88), width=1)
            d.text((x + 20, 216), label, font=_card_font(16), fill=(120, 170, 210))
            d.text((x + 20, 244), str(val)[:14], font=_card_font(30), fill=(255, 255, 255))
            x += 200
        d.text((80, 330), "Generated by @FFLikeDB_Bot", font=_card_font(18), fill=(110, 125, 150))
        path = os.path.join(CARD_DIR, f"card_{uid}.png")
        img.save(path)
        return path
    except Exception:
        return None


def text_stat_card(uid, data):
    rows = ["🎴 <b>PLAYER STAT CARD</b>", "━━━━━━━━━━━━━━━",
            f"🎯 Name: <b>{data.get('name') or '—'}</b>",
            f"🆔 UID: <code>{uid}</code>",
            f"⭐ Level: <b>{data.get('level') if data.get('level') is not None else '—'}</b>",
            f"❤️ Likes: <b>{data.get('likes') if data.get('likes') is not None else '—'}</b>",
            f"📈 EXP: <b>{data.get('exp') if data.get('exp') is not None else '—'}</b>",
            "🌍 Region: BD", "━━━━━━━━━━━━━━━", "🤖 @FFLikeDB_Bot"]
    return "\n".join(rows)


@bot.message_handler(commands=["card", "statcard"])
def cmd_card(m):
    parts = m.text.split()
    target = parts[1] if len(parts) > 1 and parts[1].isdigit() else get_saved_uid(m.from_user.id)
    if not target or not str(target).isdigit():
        bot.reply_to(m, "⚠️ Usage: <code>/card UID</code> — or save your UID first with /setuid.")
        return
    msg = bot.reply_to(m, "🎨 Building your stat card...")
    try:
        jwt = None
        pool = ff_bd_api.get_pool()
        if pool:
            jwt = pool[0]["token"]
        s = ff_bd_api.get_stats(int(target), jwt) or {}
        if s.get("error"):
            bot.edit_message_text(f"❌ Player not found: {str(s.get('error'))[:60]}", m.chat.id, msg.message_id)
            return
        full = ff_bd_api.get_full_profile(int(target), jwt) or {}
        if full.get("level") is not None or full.get("exp") is not None:
            s["level"] = full.get("level")
            s["exp"] = full.get("exp")
        path = make_stat_card(str(target), s)
        if path and os.path.exists(path):
            with open(path, "rb") as ph:
                bot.send_photo(m.chat.id, ph,
                               caption=f"🎴 <b>{s.get('name') or '—'}</b>  ·  UID <code>{target}</code>\n"
                                       f"⭐ Lv {s.get('level') or '—'}  ❤️ {s.get('likes') or '—'} likes\n"
                                       f"📤 Share your card! @FFLikeDB_Bot")
            try:
                os.remove(path)
            except Exception:
                pass
            try:
                bot.delete_message(m.chat.id, msg.message_id)
            except Exception:
                pass
        else:
            bot.edit_message_text(text_stat_card(str(target), s), m.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Could not build card: {str(e)[:80]}", m.chat.id, msg.message_id)


# ============ NEW: STYLISH NAME GENERATOR (/namegen) ============
_NAME_WORDS = ["Sabbir", "Rony", "Siam", "Tamim", "Rakib", "Nayeem", "Arif", "Shakib", "Rifat",
               "Jisan", "Sohan", "Mehedi", "Tanvir", "Imran", "Nabil", "Fahim", "Adnan", "Rasel",
               "Tuhin", "Sumon", "Hasib", "Reyad", "Sakib", "Emon", "Shanto", "Foysal", "Nadim"]
_NAME_DECOR = ["亗", "×", "么", "乡", "〆", "ツ", "彡", "々", "☬", "ʚ", "ɞ", "᭄", "┋", "₳", "×͜×",
               "☆", "✿", "꧁", "꧂", "ᴮᴰ"]
_NAME_TAIL = ["FF", "BD", "OP", "YT", "Pro", "Gamer", "King", "Boy", "007", "99", "XD", "Lover"]


def gen_names(style, n=12):
    random.shuffle(_NAME_WORDS)
    out = []
    for w in _NAME_WORDS:
        if len(out) >= n:
            break
        d1 = random.choice(_NAME_DECOR)
        d2 = random.choice(_NAME_DECOR)
        t = random.choice(_NAME_TAIL)
        if style == "short":
            pat = f"{d1}{w[:6]}{d2}"
        elif style == "gamer":
            pat = f"{d1}{w}_{t}{d2}"
        elif style == "bd":
            pat = f"{w}{d1}{t}"
        else:  # stylish
            pat = random.choice([f"{d1}{w}{d2}", f"{w}{d1}{t}{d2}", f"{d1}{w[:5]}{t}{d2}"])
        pat = pat[:22]
        if pat not in out:
            out.append(pat)
    return out


@bot.message_handler(commands=["namegen", "names"])
def cmd_namegen(m):
    parts = m.text.split()
    style = parts[1].lower() if len(parts) > 1 and not parts[1].isdigit() else "stylish"
    if style not in ("stylish", "gamer", "bd", "short"):
        style = "stylish"
    names = gen_names(style, 12)
    body = "\n".join(f"{i+1}. {nm}" for i, nm in enumerate(names))
    bot.reply_to(m,
                 f"✧ <b>STYLISH NAME GENERATOR</b> ✧\n"
                 f"Style: <b>{style}</b>  ·  in-game name limit: 12 chars\n"
                 f"━━━━━━━━━━━━━━━\n"
                 f"<code>{body}</code>\n"
                 f"━━━━━━━━━━━━━━━\n"
                 f"Tap a name to copy. Other styles:\n"
                 f"/namegen stylish · /namegen gamer · /namegen bd · /namegen short")

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


@bot.message_handler(commands=["start"])
def cmd_start(m):
    # welcome message for new vs returning users
    is_new = getattr(m, "_ff_new_user", False)
    if is_new:
        u = m.from_user
        reg_date = _bd_date(time.time())
        bot.reply_to(m,
            f"👋 Welcome, <b>{u.first_name or 'Player'}!</b>\n\n"
            f"🎉 Your account has been created!\n"
            f"🆔 Account ID: <code>{u.id}</code>\n"
            f"📅 Registration Date: {reg_date}\n"
            f"❤️ Free likes: <b>Unlimited</b>\n\n"
            f"💡 To get started, set your Free Fire UID "
            f"or tap a button below 👇",
            reply_markup=build_welcome_kb())
        return
    # referral payload: /start ref<referrer_id>
    ref_greet = ""
    parts = m.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref") and parts[1][3:].isdigit():
        try:
            record_referral(int(parts[1][3:]))
            ref_greet = "\n🎁 Welcome — you joined via a referral!"
        except Exception:
            pass
    bot.reply_to(m,
        "🔥 <b>স্বাগতম FF • FREE LIKE BOT-এ!</b> 🎮💙\n\n"
        "💖 ফ্রি ফায়ার লাইক, প্লেয়ার প্রোফাইল ও গিল্ড ফিচার—সবকিছু এক বটেই! 🚀\n\n"
        "⚡ আপনার গেমিং অভিজ্ঞতাকে আরও সহজ ও আকর্ষণীয় করতে আজই আমাদের বট ব্যবহার করুন!\n\n"
        "🎯 <b>FF • FREE LIKE BOT</b> — আপনার গেমিং, আপনার গ্লোরি! 👑\n\n"
        "👇 এখনই শুরু করুন এবং বন্ধুদের সাথেও শেয়ার করুন! 🔥"
        + ref_greet,
        reply_markup=build_menu())



@bot.message_handler(commands=["help", "menu"])
def cmd_help(m):
    txt = (
        "📖 <b>FF • FREE LIKE BOT — সব কমান্ড</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "🎮 <b>লাইক ও প্রোফাইল</b>\n"
        "<b>/like</b> <code>UID [count]</code> — লাইক পাঠান\n"
        "<b>/stats</b> <code>UID</code> — প্লেয়ারের লাইক ও নাম\n"
        "<b>/profile</b> <code>UID</code> — ফুল প্রোফাইল (লেভেল, র্যাঙ্ক)\n"
        "<b>/card</b> <code>UID</code> — 🎴 শেয়ারযোগ্য স্ট্যাট কার্ড ছবি\n"
        "<b>/rank</b> — 📊 লেভেল/EXP/লাইক দৈনিক ট্র্যাক\n\n"
        "🏰 <b>গিল্ড</b>\n"
        "<b>/guild</b> <code>GUILD_ID</code> — গিল্ড ইনফো কার্ড\n"
        "<b>/guildlike</b> <code>GUILD_ID</code> — গিল্ড মেম্বারদের লাইক\n"
        "<b>/setguild</b> <code>GUILD_ID</code> — গিল্ড সেভ করুন\n\n"
        "⚙️ <b>আপনার অ্যাকাউন্ট</b>\n"
        "<b>/setuid</b> <code>UID</code> — UID সেভ করুন (সব কমান্ডে অটো)\n"
        "<b>/myuid</b> — সেভ করা UID দেখা/বদলানো\n"
        "<b>/myid</b> — আপনার টেলিগ্রাম আইডি ও কুলডাউন\n"
        "<b>/history</b> — আপনার লাইক হিস্ট্রি\n"
        "<b>/referrals</b> — রেফার কাউন্ট ও লিংক\n"
        "<b>/stopremind</b> — দৈনিক রিমাইন্ডার বন্ধ\n\n"
        "✨ <b>অতিরিক্ত</b>\n"
        "<b>/namegen</b> — স্টাইলিশ FF নাম (style: gamer/bd/short)\n"
        "<b>/namecheck</b> <code>name</code> — নাম/ইউজারনেম খালি কিনা চেক\n"
        "<b>/redeem</b> — 🎁 অফিশিয়াল রিডিম কোড ও গাইড\n"
        "<b>/help</b> — এই লিস্ট\n"
        "<b>/start</b> — ওয়েলকাম মেনু\n"
    )
    if is_admin(m.from_user.id if m.from_user else 0):
        txt += (
            "\n👑 <b>অ্যাডমিন</b>\n"
            "<b>/admin</b> — ইউজার অ্যানালিটিক্স\n"
            "<b>/pool</b> — লাইক পুল স্ট্যাটাস\n"
            "<b>/guests</b> — গেস্ট অ্যাকাউন্ট স্ট্যাটাস\n"
            "<b>/broadcast</b> <code>msg</code> — সবাইকে মেসেজ\n"
            "<b>/addcode</b> <code>CODE | reward | days</code> — কোড যোগ\n"
            "<b>/delcode</b> <code>CODE</code> — কোড বাদ\n"
            "<b>/export_users</b> — ইউজার লিস্ট ফাইল\n"
            "<b>/claim</b> <code>CODE</code> — অ্যাডমিন কোড\n"
        )
    txt += "━━━━━━━━━━━━━━━\n👇 নিচের বাটন থেকেও শুরু করতে পারেন"
    bot.reply_to(m, txt, reply_markup=build_menu())

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



# ============ NEW: GUILD LOOKUP CARD (/guild) ============

# ============ NEW: RANK TRACKER (/rank) ============
RANKS_FILE = Path(__file__).parent / "rank_snapshots.json"
RANKS_LOCK = threading.Lock()
RANK_CHECK_EVERY = 6 * 3600   # check loop wake-up
RANK_SNAPSHOT_GAP = 20 * 3600 # minimum gap between snapshots per day
RANK_MAX_USERS = 150


def load_ranks() -> dict:
    return _load_json(RANKS_FILE)


def save_ranks(data: dict):
    with RANKS_LOCK:
        _save_json(RANKS_FILE, data)


def tracked_uids() -> dict:
    """{uid: [user_ids]} built from saved-UID reminders + users.json."""
    out = {}
    rem = _load_json(REMINDERS_FILE)
    for uid_s, info in (rem or {}).items():
        u = str((info or {}).get("uid") or "").strip()
        if u.isdigit():
            out.setdefault(u, set()).add(uid_s)
    users = _load_json(USERS_FILE) or {}
    for uid_s, rec in users.items():
        u = str((rec or {}).get("saved_uid") or "").strip()
        if u.isdigit():
            out.setdefault(u, set()).add(uid_s)
    return {u: sorted(ids) for u, ids in out.items()}


def rank_snapshot_once(force=False) -> int:
    """Take one profile snapshot for all tracked UIDs; notify owners on change."""
    ranks = load_ranks()
    now = time.time()
    if not force:
        last = float(ranks.get("_last_run") or 0)
        if now - last < RANK_SNAPSHOT_GAP:
            return 0
    uids = tracked_uids()
    if not uids:
        ranks["_last_run"] = now
        save_ranks(ranks)
        return 0
    jwt = None
    try:
        pool = ff_bd_api.get_pool()
        jwt = pool[0]["token"] if pool else None
    except Exception:
        jwt = None
    hit = 0
    for uid, owners in list(uids.items())[:RANK_MAX_USERS]:
        try:
            s = ff_bd_api.get_stats(int(uid), jwt) or {}
            if s.get("error") or not s.get("name"):
                continue
            full = ff_bd_api.get_full_profile(int(uid), jwt) or {}
            cur = {"name": s.get("name"), "likes": s.get("likes"),
                   "level": full.get("level"), "exp": full.get("exp"), "ts": now}
            old = ranks.get(uid) or {}
            ranks[uid] = cur
            hit += 1
            if old:
                changes = []
                if old.get("level") is not None and cur.get("level") is not None and old["level"] != cur["level"]:
                    d = cur["level"] - old["level"]
                    changes.append(f"⭐ Level: {old['level']} → <b>{cur['level']}</b> ({'+' if d > 0 else ''}{d})")
                if old.get("likes") is not None and cur.get("likes") is not None and old["likes"] != cur["likes"]:
                    d = cur["likes"] - old["likes"]
                    changes.append(f"❤️ Likes: {old['likes']} → <b>{cur['likes']}</b> ({'+' if d > 0 else ''}{d})")
                if old.get("exp") is not None and cur.get("exp") is not None and old["exp"] != cur["exp"]:
                    changes.append(f"📈 EXP: {old['exp']} → <b>{cur['exp']}</b>")
                if changes:
                    txt = (f"🔔 <b>PROFILE UPDATE</b>\n"
                           f"🎯 {(cur.get('name') or '—')[:20]}  ·  UID <code>{uid}</code>\n"
                           f"━━━━━━━━━━━━━━━\n" + "\n".join(changes) +
                           f"\n━━━━━━━━━━━━━━━\nFull card: /card {uid}")
                    for owner in owners:
                        try:
                            bot.send_message(int(owner), txt)
                        except Exception:
                            pass
        except Exception:
            continue
        time.sleep(1.2)
    ranks["_last_run"] = now
    save_ranks(ranks)
    try:
        _github_commit_file(RANKS_FILE.name, "update rank snapshots")
    except Exception:
        pass
    print(f"[rank] snapshot done: {hit} profiles tracked")
    return hit


def rank_watch_loop():
    time.sleep(300)
    while True:
        try:
            rank_snapshot_once()
        except Exception as e:
            print("[rank] loop error:", e)
        time.sleep(RANK_CHECK_EVERY)


@bot.message_handler(commands=["rank", "track"])
def cmd_rank(m):
    uid = get_saved_uid(m.from_user.id)
    if not uid:
        parts = m.text.split()
        if len(parts) > 1 and parts[1].isdigit():
            uid = parts[1]
        else:
            bot.reply_to(m, "⚠️ Save your UID first (/setuid UID) — then I will track your "
                            "level, EXP and likes daily and message you when they change.\n"
                            "Or check once: /rank UID")
            return
    ranks = load_ranks()
    rec = ranks.get(str(uid)) or {}
    total = len([k for k in ranks if not k.startswith("_")])
    if rec:
        when = time.strftime("%d %b %H:%M", time.localtime(rec.get("ts") or 0))
        bot.reply_to(m,
                     f"📊 <b>RANK TRACKER</b>\n"
                     f"━━━━━━━━━━━━━━━\n"
                     f"🎯 {(rec.get('name') or '—')[:20]}  ·  UID <code>{uid}</code>\n"
                     f"⭐ Level: <b>{rec.get('level') if rec.get('level') is not None else '—'}</b>\n"
                     f"❤️ Likes: <b>{rec.get('likes') if rec.get('likes') is not None else '—'}</b>\n"
                     f"📈 EXP: <b>{rec.get('exp') if rec.get('exp') is not None else '—'}</b>\n"
                     f"🕒 Last check: {when}\n"
                     f"━━━━━━━━━━━━━━━\n"
                     f"📡 Tracking {total} player(s). Changes are messaged automatically daily.\n"
                     f"🎴 Full card: /card {uid}")
    else:
        bot.reply_to(m,
                     f"📡 UID <code>{uid}</code> is now on the tracker list.\n"
                     f"First snapshot runs within a few hours, then daily.\n"
                     f"You will get a message when level, EXP or likes change.\n"
                     f"Now tracking {total} player(s).")


# ============ NEW: REDEEM CODE HUB (/redeem) ============
CODES_FILE = Path(__file__).parent / "codes.json"
REDEEM_PORTAL = "https://reward.ff.garena.com"


def load_codes() -> dict:
    return _load_json(CODES_FILE) or {}


def active_codes() -> list:
    now = time.time()
    out = []
    for code, rec in load_codes().items():
        if not isinstance(rec, dict):
            continue
        exp = rec.get("expiry")
        if exp and float(exp) < now:
            continue
        out.append((code, rec))
    out.sort(key=lambda x: float(x[1].get("added_ts") or 0), reverse=True)
    return out


@bot.message_handler(commands=["redeem", "codes"])
def cmd_redeem(m):
    codes = active_codes()
    parts = [f"🎁 <b>FREE FIRE REDEEM CODES</b>",
             "━━━━━━━━━━━━━━━",
             f"🌐 Official portal: {REDEEM_PORTAL}",
             "",
             "<b>How to redeem:</b>",
             "1. Open the portal link above",
             "2. Log in with the same account you play on",
             "3. Paste a code and confirm",
             "4. Reward arrives in your in-game mail",
             "",
             "⚠️ Only BD-server accounts. Each code works once per account."]
    if codes:
        parts.append("")
        parts.append(f"<b>Active codes ({len(codes)}):</b>")
        for code, rec in codes[:10]:
            rw = (rec.get("reward") or "reward").strip()
            parts.append(f"`{code}` — {rw[:40]}")
    else:
        parts.append("")
        parts.append("📭 No verified codes right now. New codes are added here as soon "
                     "as they are confirmed official.")
    parts.append("")
    parts.append("🛡️ Scam guard: never enter your password or UID on any site other than "
                 "the official portal above.")
    bot.reply_to(m, "\n".join(parts))


@bot.message_handler(commands=["addcode"])
def cmd_addcode(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Admin-only command.")
        return
    raw = m.text.split(maxsplit=1)
    if len(raw) < 2 or "|" not in raw[1]:
        bot.reply_to(m, "⚠️ Usage: <code>/addcode CODE | reward | days</code>\n"
                        "Example: <code>/addcode FFIC2026 | 1x Diamond Royale | 7</code>")
        return
    parts = [p.strip() for p in raw[1].split("|")]
    code = parts[0].upper()
    reward = parts[1] if len(parts) > 1 and parts[1] else "reward"
    days = 7
    if len(parts) > 2 and parts[2].isdigit():
        days = int(parts[2])
    if not code.isalnum() or len(code) < 4:
        bot.reply_to(m, "⚠️ Code must be letters/numbers, at least 4 characters.")
        return
    codes = load_codes()
    codes[code] = {"reward": reward[:60], "added_by": m.from_user.id,
                   "added_ts": time.time(), "expiry": time.time() + days * 86400}
    _save_json(CODES_FILE, codes)
    threading.Thread(target=_github_commit_file, args=(CODES_FILE.name, "update redeem codes"),
                     daemon=True).start()
    bot.reply_to(m, f"✅ Code <code>{code}</code> added for {days} day(s).\n"
                    f"Users can see it via /redeem.")


@bot.message_handler(commands=["delcode"])
def cmd_delcode(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Admin-only command.")
        return
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(m, "⚠️ Usage: <code>/delcode CODE</code>")
        return
    code = parts[1].upper()
    codes = load_codes()
    if code not in codes:
        bot.reply_to(m, f"⚠️ <code>{code}</code> is not in the list.")
        return
    codes.pop(code, None)
    _save_json(CODES_FILE, codes)
    threading.Thread(target=_github_commit_file, args=(CODES_FILE.name, "remove redeem code"),
                     daemon=True).start()
    bot.reply_to(m, f"🗑️ Code <code>{code}</code> removed.")


# ============ NEW: USERNAME / NICKNAME CHECKER (/namecheck) ============
def _tg_username_status(uname: str) -> str:
    """Check Telegram username availability via the public t.me preview."""
    try:
        r = httpx.get(f"https://t.me/{uname}", timeout=12, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"})
        t = r.text
        if "tgme_page_title" in t or "tgme_page_extra" in t:
            return "taken"
        if "tgme_page" in t:
            return "free"
        return "unknown"
    except Exception:
        return "error"


def _tg_username_problems(uname: str) -> list:
    out = []
    if not (5 <= len(uname) <= 32):
        out.append("length must be 5–32 characters")
    if not uname[:1].isalpha():
        out.append("must start with a letter")
    if not re.match(r"^[A-Za-z0-9_]+$", uname):
        out.append("only letters, numbers and underscore allowed")
    if "__" in uname:
        out.append("no double underscore (__)")
    if uname.endswith("_"):
        out.append("cannot end with underscore")
    return out


def _ff_nick_problems(nick: str) -> list:
    out = []
    if len(nick) < 4:
        out.append("too short for Free Fire (min ~4)")
    elif len(nick) > 13:
        out.append("too long — Free Fire rejects most names over 13 chars")
    if nick != nick.strip():
        out.append("leading/trailing space breaks the name")
    if "  " in nick:
        out.append("double space is rejected")
    return out


def _name_variants(base: str, n: int = 5) -> list:
    clean = re.sub(r"[^A-Za-z0-9_]", "", base) or "Player"
    out = []
    for i in range(n):
        suf = random.choice(["_BD", "FF", "_99", "_OP", "007", "X", "_YT", "01", "BD", "_Pro"])
        v = (clean[:26] + suf)[:32]
        if v not in out:
            out.append(v)
    return out



_INVIS = {0x3164: "HANGUL FILLER", 0x2800: "BRAILLE BLANK", 0x200B: "ZERO-WIDTH SPACE",
          0x200C: "ZERO-WIDTH NON-JOINER", 0x200D: "ZERO-WIDTH JOINER", 0xFEFF: "BOM",
          0x00A0: "NO-BREAK SPACE", 0x2007: "FIGURE SPACE", 0x202F: "NARROW NO-BREAK SPACE",
          0x115F: "HANGUL CHOSEONG FILLER", 0x1160: "HANGUL JUNGSEONG FILLER"}


def _invis_report(name: str):
    """Return (count, detail_lines, cleaned) for hidden/zero-width characters."""
    hits = {}
    clean = []
    for ch in name:
        cp = ord(ch)
        if cp in _INVIS or (0x200B <= cp <= 0x200F) or (0x202A <= cp <= 0x202E):
            hits[cp] = hits.get(cp, 0) + 1
            continue
        clean.append(ch)
    lines = []
    for cp, n in hits.items():
        label = _INVIS.get(cp, "HIDDEN CHARACTER")
        lines.append(f"• U+{cp:04X} {label} ×{n}")
    return sum(hits.values()), lines, "".join(clean)

@bot.message_handler(commands=["namecheck", "uname"])
def cmd_namecheck(m):
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(m, "⚠️ Usage: <code>/namecheck your_name</code>\n"
                        "Checks Telegram username availability + Free Fire nickname rules.")
        return
    raw = parts[1].strip().lstrip("@")
    tg_problems = _tg_username_problems(raw)
    status = _tg_username_status(raw) if not tg_problems else None
    ff_problems = _ff_nick_problems(raw)
    lines = [f"🔎 <b>NAME CHECK</b>  ·  <code>{raw}</code>", "━━━━━━━━━━━━━━━",
             "<b>Telegram username</b>"]
    if tg_problems:
        lines.append("❌ invalid: " + "; ".join(tg_problems))
    elif status == "free":
        lines.append("✅ <b>available</b> — you can claim it")
    elif status == "taken":
        lines.append("❌ <b>already taken</b>")
    elif status == "error":
        lines.append("⚠️ could not check right now (network) — try again")
    else:
        lines.append("⚠️ status unclear — check in the Telegram app")
    lines.append("")
    lines.append("<b>Free Fire nickname</b>")
    if ff_problems:
        lines.append("⚠️ " + "; ".join(ff_problems))
    elif len(raw) < 6:
        lines.append("⚠️ usable, but 6+ characters look better in-game")
    else:
        lines.append("✅ format looks valid for Free Fire")
    inv_count, inv_lines, cleaned = _invis_report(raw)
    if inv_count:
        lines.append("")
        lines.append(f"👻 <b>Hidden characters: {inv_count}</b>")
        lines.extend(inv_lines)
        lines.append(f"👁 Visible version: <code>{cleaned}</code>")
        lines.append("ℹ️ These render as spaces in-game. Rename/search usually matches "
                     "the VISIBLE text, so people can copy the look with a plain space.")
    lines.append("")
    lines.append("🎮 <b>Verify in 10 seconds (free, no card needed):</b>")
    lines.append("1. Open Free Fire → Friends → Add Friend → Search by nickname")
    lines.append("2. Paste the exact name and search")
    lines.append("3. Player found = ❌ not available · No result = ✅ available")
    lines.append("4. Confirm finally on the name-change screen before spending a card")
    lines.append("")
    lines.append("ℹ️ Garena has no public name→player API, so this bot cannot query it directly.")
    if status == "taken" or ff_problems:
        lines.append("")
        lines.append("<b>Try these instead:</b>")
        lines.append("<code>" + "\n".join(_name_variants(raw)) + "</code>")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("✧ More ideas: /namegen")
    bot.reply_to(m, "\n".join(lines))

@bot.message_handler(commands=["guild"])
def cmd_guild(m):
    parts = m.text.split()
    clan = parts[1] if len(parts) > 1 and parts[1].isdigit() else get_saved_guild(m.from_user.id)
    if not clan or not str(clan).isdigit():
        bot.reply_to(m, "⚠️ Usage: <code>/guild GUILD_ID</code>\n"
                        "Find the Guild ID in-game: Guild → Info (bottom of the page).")
        return
    msg = bot.reply_to(m, "🔍 Looking up guild...")
    try:
        g = ff_bd_api.get_guild(int(clan))
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:70]}", m.chat.id, msg.message_id)
        return
    if not g or not g.get("name"):
        bot.edit_message_text("❌ Guild not found. Check the Guild ID (BD server guilds only).",
                              m.chat.id, msg.message_id)
        return
    mem = int(g.get("members_total") or 0)
    cap = int(g.get("capacity") or 0)
    free = max(cap - mem, 0)
    pct = int(mem / cap * 100) if cap else 0
    filled = max(0, min(10, round(pct / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    active = len(g.get("uids") or [])
    captain = g.get("captain")
    card = (
        f"🏰 <b>{(g.get('name') or '—')[:24]}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🆔 Guild ID: <code>{g.get('clan_id')}</code>\n"
        f"⭐ Level: <b>{g.get('level') if g.get('level') is not None else '—'}</b>\n"
        f"🌍 Region: <b>{g.get('region') or 'BD'}</b>\n"
        f"👥 Members: <b>{mem}/{cap}</b>  ({pct}%)\n"
        f"<code>{bar}</code>\n"
        f"🟢 Active tracked: <b>{active}</b>\n"
        f"{'🔓 Open slots: <b>' + str(free) + '</b>' + chr(10) if free else '🔒 Guild is full' + chr(10)}"
        f"{'👑 Captain UID: <code>' + str(captain) + '</code>' + chr(10) if captain else ''}"
        f"━━━━━━━━━━━━━━━\n"
        f"❤️ Like members: /guildlike {g.get('clan_id')}\n"
        f"📌 Save this guild: /setguild {g.get('clan_id')}"
    )
    bot.edit_message_text(card, m.chat.id, msg.message_id)

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
    threading.Thread(target=rank_watch_loop, daemon=True).start()
    bot.infinity_polling(skip_pending=True)
