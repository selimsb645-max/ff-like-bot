#!/usr/bin/env python3
"""STEP 3 — Super Admin panel tests (live server).

    python3 tests/test_admin_panel.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request

BASE = os.environ.get("NAI_ADMIN", "https://solas-5609a435.base44.app/functions/naiAdmin")
API = os.environ.get("NAI_API", "https://solas-5609a435.base44.app/functions/naiAccess")
SECRET = os.environ.get("NAI_SESSION_SECRET", "naiPanel-2026-session-7k2q9x4mL1")
INTERNAL = "solasAccess7k2q9x4mL1"
OWNER = "8012564523"

out: list[tuple[str, str, str]] = []


def rec(case: str, ok: bool, detail: str = "") -> None:
    out.append((case, "PASS" if ok else "FAIL", detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {case}" + (f"  ·  {detail}" if detail else ""), flush=True)


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def token(kind: str, ttl_min: int = 10, tg: str = OWNER, role: str = "superadmin") -> str:
    body = b64u(json.dumps({"tg": tg, "role": role, "kind": kind,
                            "exp": int(time.time() * 1000) + ttl_min * 60000}).encode())
    mac = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call(path: str, method: str = "GET", cookie: str = "", payload: dict | None = None,
         panel: bool = True, follow: bool = True) -> tuple[int, str, dict]:
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = cookie
    if panel:
        h["X-Panel"] = "1"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=30) as res:
            return res.status, res.read().decode("utf-8", "replace"), dict(res.headers)
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode("utf-8", "replace"), dict(ex.headers)


def api(path: str, cookie: str, method: str = "GET", payload=None) -> tuple[int, dict]:
    st, body, _ = call(path, method, cookie, payload)
    try:
        return st, json.loads(body)
    except Exception:
        return st, {}


def access(action: str, **kw) -> tuple[int, dict]:
    req = urllib.request.Request(API, data=json.dumps({"action": action, **kw}).encode(),
                                 headers={"Content-Type": "application/json", "x-nai-internal": INTERNAL})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as ex:
        try:
            return ex.code, json.loads(ex.read() or b"{}")
        except Exception:
            return ex.code, {}


def main() -> int:
    print("\n=== 1. UNAUTHORIZED ACCESS ===", flush=True)
    st, body, _ = call("?r=api/stats")
    rec("dashboard API refuses a request with no session", st == 401, f"HTTP {st}")
    st, body, _ = call("?r=api/users")
    rec("user list refused without session", st == 401)
    st, body, _ = call("")
    rec("panel HTML never renders without a session",
        "NAI ACCESS · ADMIN" not in body and "Sign in" in body, f"HTTP {st}")

    st, body, _ = call("?r=api/stats", cookie=f"nai_admin={token('link')}")
    rec("a 'link' token cannot be used as a session cookie", st == 401)
    st, body, _ = call("?r=api/stats", cookie="nai_admin=abc.def")
    rec("garbage cookie refused", st == 401)
    good = token("session")
    forged = good[:-4] + "0000"
    st, body, _ = call("?r=api/stats", cookie=f"nai_admin={forged}")
    rec("forged signature refused", st == 401)
    expired = token("session", ttl_min=-5)
    st, body, _ = call("?r=api/stats", cookie=f"nai_admin={expired}")
    rec("expired session refused", st == 401)
    other = token("session", tg="123456789")
    st, body, _ = call("?r=api/stats", cookie=f"nai_admin={other}")
    record = st == 401
    rec("session for another Telegram id is refused", record, f"HTTP {st}")

    st, body, _ = call("", follow=False)
    rec("login page is served", st == 200 and "SUPER ADMIN" in body)
    leaked = INTERNAL in body or SECRET in body
    rec("login page exposes no shared secret", not leaked)

    print("\n=== 2. SUPER ADMIN SESSION ===", flush=True)
    link = f"{BASE}?r=link&t={token('link')}"
    st, body, headers = call(f"?r=link&t={token('link')}")
    setcookie = headers.get("Set-Cookie", "")
    rec("signed link signs the admin in", st == 200 and "nai_admin=" in setcookie, f"HTTP {st}")
    cookie = setcookie.split(";")[0]
    st, body, _ = call("", cookie=cookie)
    rec("dashboard opens with the session", st == 200 and "NAI ACCESS · ADMIN" in body)
    rec("dashboard HTML contains no shared secret",
        INTERNAL not in body and SECRET not in body and "TELEGRAM_BOT_TOKEN" not in body)

    print("\n=== 3. DASHBOARD DATA ===", flush=True)
    st, d = api("?r=api/stats", cookie)
    keys = ["users", "active", "pending", "expired", "revoked", "blocked", "codes_today", "events_today"]
    rec("stats returns every KPI", st == 200 and d.get("ok") and all(k in d for k in keys),
        f"{d.get('users')} users, {d.get('active')} active")
    st, d = api("?r=api/me", cookie)
    rec("profile reports the super admin", st == 200 and d.get("telegram_id") == OWNER)
    st, d = api("?r=api/users", cookie)
    rec("user list loads", st == 200 and isinstance(d.get("users"), list), f"{d.get('total')} users")
    st, d2 = api("?r=api/installations", cookie)
    rec("installation list loads", st == 200 and isinstance(d2.get("installations"), list),
        f"{d2.get('total')} installs")
    rec("installation ids are masked in the list",
        all(i.get("masked", "").startswith("••••••") for i in d2.get("installations", [])))
    rec("IPs are masked", all("×" in i.get("ip", "×") for i in d2.get("installations", [])))
    st, d3 = api("?r=api/codes", cookie)
    rec("code list loads and never shows a hash",
        st == 200 and all("code_hash" not in c for c in d3.get("codes", [])) and
        not any(len(str(c.get("code_id", ""))) > 12 for c in d3.get("codes", [])))
    st, d4 = api("?r=api/events", cookie)
    rec("security events load", st == 200 and isinstance(d4.get("events"), list), f"{d4.get('total')} events")
    st, d5 = api("?r=api/events&type=activation", cookie)
    rec("event type filter works", st == 200 and all(e["type"] == "activation" for e in d5.get("events", [])))
    st, d6 = api("?r=api/events&type=panel_login", cookie)
    rec("login events are recorded", st == 200 and (d6.get("total", 0) > 0), f"{d6.get('total')} logins")
    st, d7 = api("?r=api/settings", cookie)
    rec("settings load with server bounds", st == 200 and len(d7.get("settings", [])) >= 6)

    print("\n=== 4. VALIDATION AND ACTIONS ===", flush=True)
    st, raw, _ = call("?r=api/settings", "POST", cookie, {"key": "max_active_installations", "value": "99"})
    rec("out of range setting is refused", st == 400, json.loads(raw).get("error", "")[:44])
    st, raw, _ = call("?r=api/settings", "POST", cookie, {"key": "unknown_key", "value": "1"})
    rec("unknown setting is refused", st == 400)
    st, raw, _ = call("?r=api/settings", "POST", cookie, {"key": "max_active_installations", "value": "3"})
    rec("valid setting is saved", st == 200 and json.loads(raw).get("value") == "3")
    st, d = api("?r=api/settings", cookie)
    kept = next((s["value"] for s in d.get("settings", []) if s["key"] == "max_active_installations"), None)
    rec("setting persists", kept == "3", f"value={kept}")

    st, raw, _ = call("?r=api/action", "POST", cookie, {"action": "block", "target": "1"})
    rec("action without confirmation is refused", st == 400)
    st, raw, _ = call("?r=api/action", "POST", "", {"action": "block", "target": "1", "confirm": True}, panel=False)
    rec("action without a session is refused", st == 401)
    st, raw, _ = call("?r=api/stats", "POST", cookie, {}, panel=False)
    rec("POST without the panel header is refused", st == 400)

    # a fresh user + installation to act on
    tg = "9" + str(secrets.randbelow(10**9)).zfill(9)
    iid = secrets.token_hex(16)
    _, gen = access("generate", tg_user_id=tg, display_name="panel test")
    code = gen.get("code", "")
    access("activate", code=code, installation_id=iid)

    st, raw, _ = call("?r=api/action", "POST", cookie, {"action": "block", "target": tg, "confirm": True})
    rec("block works from the panel", st == 200)
    time.sleep(16)
    st2, _ = access("generate", tg_user_id=tg)
    rec("blocked user cannot get a code", st2 == 403)
    st, raw, _ = call("?r=api/action", "POST", cookie, {"action": "unblock", "target": tg, "confirm": True})
    rec("unblock works", st == 200)

    st, raw, _ = call("?r=api/action", "POST", cookie, {"action": "revoke", "target": iid, "confirm": True})
    rec("revoke works from the panel", st == 200)
    vst, v = access("verify", installation_id=iid)
    rec("revoked device is refused at the next verification", vst == 403 and v.get("status") == "REVOKED")

    st, raw, _ = call("?r=api/action", "POST", cookie, {"action": "reset", "target": iid, "confirm": True})
    rec("reset works", st == 200)
    vst, v = access("verify", installation_id=iid)
    rec("reset device must activate again", vst in (403, 404))

    _, gen2 = access("generate", tg_user_id=tg)
    code2 = gen2.get("code", "")
    st, d = api("?r=api/codes", cookie)
    row = next((c for c in d.get("codes", []) if c.get("telegram_user_id") == tg and c.get("status") == "issued"), None)
    if row:
        st, raw, _ = call("?r=api/action", "POST", cookie, {"action": "revoke_code", "target": row["id"], "confirm": True})
        rec("unused code can be revoked", st == 200)
        ast, a = access("activate", code=code2, installation_id=secrets.token_hex(16))
        rec("revoked code cannot activate", ast in (403, 409), a.get("status", ""))
    else:
        rec("unused code can be revoked", False, "no issued code found")

    st, d = api("?r=api/users/" + tg, cookie)
    rec("user detail returns history",
        st == 200 and "installations" in d and "codes" in d and "events" in d)

    st, d = api("?r=api/events", cookie)
    has_admin = any(e["type"] == "admin_action" for e in d.get("events", []))
    rec("admin actions are written to the audit trail", has_admin, "admin_action present")

    print("\n=== 5. EXISTING SYSTEMS STILL WORK ===", flush=True)
    req = urllib.request.Request(
        "https://solas-5609a435.base44.app/functions/naiBot",
        data=json.dumps({"update_id": 9500, "debug": True, "message": {
            "chat": {"id": OWNER}, "from": {"id": OWNER, "first_name": "Selim"}, "text": "/start"}}).encode(),
        headers={"Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": INTERNAL})
    with urllib.request.urlopen(req, timeout=30) as res:
        bot = json.loads(res.read() or b"{}")
    rec("Telegram bot still answers /start", bot.get("menu") is True)

    st, d = access("status", tg_user_id=tg)
    rec("access API still answers status", st == 200 and d.get("ok") is True)

    passed = sum(1 for _, r, _ in out if r == "PASS")
    print(f"\n{passed}/{len(out)} passed", flush=True)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel_step3.json"), "w") as fh:
        json.dump([{"case": c, "result": r, "detail": d} for c, r, d in out], fh, indent=2)
    return 0 if passed == len(out) else 1


if __name__ == "__main__":
    raise SystemExit(main())
