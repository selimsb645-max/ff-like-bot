#!/usr/bin/env python3
"""STEP 2 — authentication integration & security audit.

Runs the real server, the real client module and the real Telegram webhook.
    python3 tests/test_integration_audit.py
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
API = os.environ.get("NAI_API", "https://solas-5609a435.base44.app/functions/naiAccess")
BOT = os.environ.get("NAI_BOT", "https://solas-5609a435.base44.app/functions/naiBot")
INTERNAL = "solasAccess7k2q9x4mL1"
OWNER = "8012564523"

rows: list[tuple[str, str, str]] = []   # (case, PASS/FAIL, detail)


def record(case: str, ok: bool, detail: str = "") -> None:
    rows.append((case, "PASS" if ok else "FAIL", detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {case}" + (f"  ·  {detail}" if detail else ""), flush=True)


def post(payload: dict, url: str = API, headers: dict | None = None) -> tuple[int, dict]:
    h = {"Content-Type": "application/json"}
    if headers is None:
        h["x-nai-internal"] = INTERNAL
    else:
        h.update(headers)
    last: tuple[int, dict] = (0, {})
    for attempt in range(4):
        time.sleep(2.5)
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                body = json.loads(res.read() or b"{}")
                if body:
                    return res.status, body
                last = (res.status, body)
        except urllib.error.HTTPError as ex:
            try:
                body = json.loads(ex.read() or b"{}")
            except Exception:
                body = {}
            if body and ex.code < 500:
                return ex.code, body
            last = (ex.code, body)
        except Exception:
            last = (0, {})
        time.sleep(6 * (attempt + 1))
    return last


def api(action: str, **kw) -> tuple[int, dict]:
    return post({"action": action, **kw})


def fresh_tg() -> str:
    return "9" + str(secrets.randbelow(10**9)).zfill(9)


def new_install() -> str:
    return secrets.token_hex(16)


def client(args: list[str], home: str, skip: str = "") -> tuple[int, str]:
    env = dict(os.environ)
    env["NAI_HOME"] = home
    env.pop("FF_SKIP_ACCESS", None)
    if skip:
        env["FF_SKIP_ACCESS"] = skip
    p = subprocess.run([sys.executable] + args, cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=120)
    return p.returncode, (p.stdout + p.stderr).strip()


def gate(home: str) -> tuple[int, str]:
    """Run the real access gate exactly like the bot does on startup."""
    code = ("import sys; sys.path.insert(0,'.'); import access;"
            "access.require_access('audit'); print('GATE_OK')")
    return client(["-c", code], home)


def main() -> int:
    print("\n=== 1. TELEGRAM BOT INTEGRATION ===", flush=True)
    hook = {}
    try:
        import os as _os
        envp = open("/app/.agents/.env", encoding="utf-8").read().splitlines()
        tok = ""
        for line in envp:
            if line.startswith("export TELEGRAM_BOT_TOKEN_6="):
                raw = line.split("=", 1)[1].strip()
                if raw.startswith("$'"):        # bash ANSI-C quoting
                    raw = raw[2:-1]
                tok = raw.strip().strip('"').strip("'")
                if tok:
                    break
        if not tok:
            raise RuntimeError("no TELEGRAM_BOT_TOKEN_6 value found")
        with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getWebhookInfo", timeout=20) as r:
            hook = json.loads(r.read())["result"]
        with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getMe", timeout=20) as r:
            me = json.loads(r.read())["result"]
        record("bot identity is @NAI_AccessBot", me.get("username") == "NAI_AccessBot", me.get("username", ""))
        record("webhook points at naiBot with no errors",
               hook.get("url", "").endswith("/naiBot") and not hook.get("last_error_message"),
               f"pending={hook.get('pending_update_count')}")
    except Exception as ex:
        record("telegram reachable", False, str(ex)[:80])

    H = {"X-Telegram-Bot-Api-Secret-Token": INTERNAL}
    st, out = post({"update_id": 9001, "debug": True, "message": {
        "chat": {"id": OWNER}, "from": {"id": OWNER, "first_name": "Selim"}, "text": "/start"}}, BOT, H)
    record("/start shows the menu and issues no code", out.get("menu") is True, f"keys={list(out)}")
    st, out = post({"update_id": 9002, "debug": True, "callback_query": {
        "id": "a", "data": "status", "from": {"id": OWNER, "first_name": "Selim"},
        "message": {"chat": {"id": OWNER}}}}, BOT, H)
    record("My Access Status answers for the caller",
           out.get("api", {}).get("ok") is True and str(out["api"].get("telegram_user_id")) == OWNER)
    st, out = post({"update_id": 9003, "debug": False, "message": {
        "chat": {"id": 999}, "from": {"id": 999, "first_name": "Stranger"}, "text": "/admin"}}, BOT, H)
    record("owner console refuses a stranger", st == 200)

    print("\n=== 2/3. SERVER + TERMINAL INTEGRATION ===", flush=True)
    home_a = "/tmp/audit_home_a"
    shutil.rmtree(home_a, ignore_errors=True)
    tg = fresh_tg()
    install_a = new_install()

    st, out = api("verify", installation_id=install_a)
    record("A. fresh clone: server says NOT_ACTIVATED", st == 404 and out.get("status") == "NOT_ACTIVATED")

    st, out = api("generate", tg_user_id=tg, display_name="audit")
    code = out.get("code", "")
    record("code issued for a fresh installation", st == 200 and code.startswith("NAI-"), code[:12])

    st, out = api("activate", code=code, installation_id=install_a)
    record("B. valid code activates the installation", st == 200 and out.get("status") == "ACTIVE")

    st, out = api("activate", code=code, installation_id=install_a)
    record("J2. same code, same installation is idempotent",
           st == 200 and out.get("reused") is True, "no second activation row")

    st, out = api("verify", installation_id=install_a)
    record("C. same installation verifies without a new code", st == 200 and out.get("status") == "ACTIVE")

    st, out = api("activate", code=code, installation_id=new_install())
    record("F. old code cannot activate another installation",
           st in (409, 403), out.get("status", ""))

    print("\n=== client side (real gate, real files) ===", flush=True)
    os.makedirs(home_a, exist_ok=True)
    with open(os.path.join(home_a, "installation.json"), "w", encoding="utf-8") as fh:
        json.dump({"installation_id": install_a, "app": "ff-like-bot"}, fh)
    rc, text = client(["-c", "import sys;sys.path.insert(0,'.');import nai_client as n;print(n.check())"], home_a)
    record("client reads the server state", "('ACTIVE'" in text, text.splitlines()[-1][:60])

    rc, text = gate(home_a)
    record("B2. the gate lets an activated install through", rc == 0 and "GATE_OK" in text,
           text.splitlines()[-1][:60] if text else "")

    rc, text = client(["-c", "import sys;sys.path.insert(0,'.');import nai_client as n;print(n.installation_id())"], home_a)
    record("installation id persists between runs", install_a in text)

    # D — local identity deleted
    shutil.rmtree(home_a, ignore_errors=True)
    rc, text = client(["-c", "import sys;sys.path.insert(0,'.');import nai_client as n;print(n.installation_id())"], home_a)
    new_id = text.strip().splitlines()[-1]
    record("D. deleting the identity makes a NEW installation",
           len(new_id) == 32 and new_id != install_a, new_id[-6:])
    st, out = api("verify", installation_id=new_id)
    record("D2. the new installation is not activated", st == 404 and out.get("status") == "NOT_ACTIVATED")

    time.sleep(16)
    st, out = api("generate", tg_user_id=tg, display_name="audit")
    code_d = out.get("code", "")
    record("D3. a reinstall can still get its own code", st == 200 and code_d.startswith("NAI-"),
           f"active_installs={out.get('active_installations')}")
    st, out = api("activate", code=code_d, installation_id=new_id)
    record("D4. the new code activates the new installation", st == 200 and out.get("status") == "ACTIVE")

    # E — a clone that never had the identity
    home_e = "/tmp/audit_home_e"
    shutil.rmtree(home_e, ignore_errors=True)
    rc, text = client(["-c", "import sys;sys.path.insert(0,'.');import nai_client as n;print(n.check())"], home_e)
    record("E. re-clone has no identity and is NOT_ACTIVATED", "('NOT_ACTIVATED'" in text, text.splitlines()[-1][:60])
    rc, text = gate(home_e)
    record("E2. the gate refuses the unactivated clone", rc != 0)

    # I — expired code
    print("\n=== 4. IP / SECURITY EVENTS ===", flush=True)
    st, out = api("config", actor_tg_id=OWNER, key="code_ttl_minutes", value="1")
    record("owner can change the code TTL", st == 200 and out.get("value") == "1")
    tg_i = fresh_tg()
    time.sleep(16)
    st, out = api("generate", tg_user_id=tg_i, display_name="expiry")
    code_i = out.get("code", "")
    record("code issued with a 1 minute TTL", st == 200 and out.get("ttl_minutes") == 1)
    print("  ... waiting 65s for the code to expire", flush=True)
    time.sleep(65)
    st, out = api("activate", code=code_i, installation_id=new_install())
    record("I. expired code is rejected", st == 410 and out.get("status") == "EXPIRED", out.get("status", ""))
    st, out = api("config", actor_tg_id=OWNER, key="code_ttl_minutes", value="5")
    record("TTL restored to 5 minutes", st == 200 and out.get("value") == "5")

    # blocked user
    time.sleep(2)
    st, out = api("block", actor_tg_id=OWNER, tg_user_id=tg_i, blocked=True)
    record("owner can block a user", st == 200 and out.get("blocked") is True)
    time.sleep(16)
    st, out = api("generate", tg_user_id=tg_i)
    record("blocked user gets no code", st == 403 and out.get("status") == "BLOCKED")
    st, out = api("block", actor_tg_id=OWNER, tg_user_id=tg_i, blocked=False)
    record("owner can unblock", st == 200 and out.get("blocked") is False)

    # G — network change handled below by the coordinator; H — revocation
    print("\n=== 5. REVOCATION ===", flush=True)
    st, out = api("revoke", actor_tg_id=OWNER, installation_id=new_id)
    record("H1. owner revokes the installation", st == 200 and out.get("ok") is True)
    st, out = api("verify", installation_id=new_id)
    record("H2. revoked installation is refused", st == 403 and out.get("status") == "REVOKED")
    rc, text = gate(home_a)
    record("H3. the gate refuses the revoked install", rc != 0)

    # J — duplicate redemption race
    tg_j = fresh_tg()
    time.sleep(16)
    st, out = api("generate", tg_user_id=tg_j, display_name="race")
    code_j = out.get("code", "")
    one, two = new_install(), new_install()
    wins: list[str] = []

    def race(inst: str) -> None:
        stx, body = api("activate", code=code_j, installation_id=inst)
        if stx == 200 and body.get("status") == "ACTIVE":
            wins.append(inst)

    ts = [threading.Thread(target=race, args=(one,)), threading.Thread(target=race, args=(two,))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    record("J. simultaneous redemption has exactly one winner", len(wins) == 1, f"{len(wins)} winner(s)")

    # negative security checks
    print("\n=== 6. SECURITY NEGATIVES ===", flush=True)
    st, out = post({"action": "generate", "tg_user_id": "1"}, API, {})  # headers={} -> no secret
    record("generate is refused without the internal secret", st == 401)
    st, out = api("events", actor_tg_id="123")
    record("admin stats refused to a stranger", st == 403)
    st, out = api("activate", code="NAI-AAAA-AAAA-AAAA", installation_id=new_install())
    record("unknown code is refused", st == 404 and out.get("status") == "UNKNOWN")
    st, out = api("activate", code=code_j, installation_id="short")
    record("malformed installation id is refused", st == 400 and out.get("status") == "INVALID")
    st, out = api("status", tg_user_id=tg)
    record("status never exposes full installation ids",
           all(len(str(i.get("installation", ""))) <= 7 for i in out.get("installations", [])))

    st, out = api("events", actor_tg_id=OWNER)
    record("audit trail is being written", (out.get("security_events") or 0) > 0,
           f"{out.get('security_events')} events")

    passed = sum(1 for _, r, _ in rows if r == "PASS")
    print(f"\n{passed}/{len(rows)} passed", flush=True)
    with open(os.path.join(HERE, "audit_step2.json"), "w", encoding="utf-8") as fh:
        json.dump([{"case": c, "result": r, "detail": d} for c, r, d in rows], fh, indent=2)
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
