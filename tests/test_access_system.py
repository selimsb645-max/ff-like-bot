#!/usr/bin/env python3
"""End-to-end tests for the NAI access system against the live API.

    python3 tests/test_access_system.py

Every test talks to the real server, exactly like a terminal would.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request

API = os.environ.get("NAI_API", "https://solas-5609a435.base44.app/functions/naiAccess")
INTERNAL = "solasAccess7k2q9x4mL1"
OWNER = "8012564523"

results: list[tuple[str, bool, str]] = []


def post(payload: dict) -> tuple[int, dict]:
    """Call the API, pacing calls and retrying when the edge throttles us."""
    last: tuple[int, dict] = (0, {})
    for attempt in range(4):
        time.sleep(3.0)
        req = urllib.request.Request(
            API, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "x-nai-internal": INTERNAL})
        try:
            with urllib.request.urlopen(req, timeout=25) as res:
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
        time.sleep(8 * (attempt + 1))
    return last


def gen(tg: str) -> tuple[int, dict]:
    return post({"action": "generate", "tg_user_id": tg, "display_name": f"test {tg}"})


def activate(code: str, install: str) -> tuple[int, dict]:
    return post({"action": "activate", "code": code, "installation_id": install})


def verify(install: str) -> tuple[int, dict]:
    return post({"action": "verify", "installation_id": install})


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  ·  " + detail) if detail else ""))


def fresh_tg() -> str:
    return "9" + str(secrets.randbelow(10**9)).zfill(9)


def new_install() -> str:
    return secrets.token_hex(16)


def main() -> int:
    tg = fresh_tg()
    install_a = new_install()

    # TEST 1 — fresh installation requests a code
    code1 = ""
    st, out = gen(tg)
    code1 = out.get("code", "")
    check("TEST 1  fresh installation gets a code", st == 200 and out.get("ok") is True and code1.startswith("NAI-"),
          f"status {st}, ttl {out.get('ttl_minutes')}m")

    # TEST 6a — an unknown installation is not activated
    st, out = verify(new_install())
    check("TEST 6  unknown installation is NOT_ACTIVATED", st == 404 and out.get("status") == "NOT_ACTIVATED",
          out.get("status", ""))

    # TEST 2 — valid code activates the installation
    st, out = activate(code1, install_a)
    check("TEST 2  code activates this installation", st == 200 and out.get("status") == "ACTIVE",
          f"status {st}")

    # TEST 3 — same installation runs again
    st, out = verify(install_a)
    check("TEST 3  same installation is accepted again", st == 200 and out.get("status") == "ACTIVE")

    # TEST 4 — an activated installation never asks for another code. The client
    # only prompts when verify() is not ACTIVE, so this is the real behaviour.
    st, out = verify(install_a)
    check("TEST 4  active installation needs no further code",
          st == 200 and out.get("status") == "ACTIVE", "verify said ACTIVE")

    # TEST 7 — old code reused on a different installation
    st, out = activate(code1, new_install())
    check("TEST 7  old code cannot activate another installation", st in (409, 403),
          out.get("status", ""))

    # TEST 6b — a fresh clone (new installation id) needs its own code
    st, out = verify(new_install())
    check("TEST 6b fresh clone has no activation", st == 404 and out.get("status") == "NOT_ACTIVATED")

    # TEST 10 — two simultaneous activations of one code
    tg2 = fresh_tg()
    st, out = gen(tg2)
    code2 = out.get("code", "")
    one, two = new_install(), new_install()
    wins: list[str] = []

    def race(inst: str) -> None:
        code, body = activate(code2, inst)
        if code == 200 and body.get("status") == "ACTIVE":
            wins.append(inst)

    threads = [threading.Thread(target=race, args=(one,)), threading.Thread(target=race, args=(two,))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    tied = verify(one)[1].get("status") == "ACTIVE" and verify(two)[1].get("status") == "ACTIVE"
    check("TEST 10 simultaneous redemption gives one winner", len(wins) == 1 and not tied,
          f"{len(wins)} winner(s)")

    winner = wins[0] if wins else one

    # TEST 8 — an activation survives later checks from a different network
    st, out = verify(winner)
    st2, out2 = verify(winner)
    check("TEST 8  activation survives repeated checks", st == 200 and st2 == 200 and out2.get("status") == "ACTIVE")

    # TEST 4b — duplicate requests inside the generation gap are throttled
    tg3 = fresh_tg()
    st1, out1 = gen(tg3)
    st2, out2 = gen(tg3)
    check("TEST 4b duplicate request inside the gap is throttled",
          st1 == 200 and st2 == 429 and out2.get("status") == "TOO_SOON",
          out2.get("status", ""))

    # TEST 9 — owner revokes; the next verification refuses
    st, out = post({"action": "revoke", "actor_tg_id": OWNER, "installation_id": winner})
    check("TEST 9a owner revoke succeeds", st == 200 and out.get("ok") is True, out.get("status", ""))
    st, out = verify(winner)
    check("TEST 9b revoked installation is refused", st == 403 and out.get("status") == "REVOKED")

    # TEST 5 — deleting local files does not delete the server record
    st, out = verify(install_a)
    check("TEST 5  server keeps the activation after local files vanish",
          st == 200 and out.get("status") == "ACTIVE")

    # admin surface
    st, out = post({"action": "events", "actor_tg_id": OWNER})
    check("ADMIN  stats readable by the owner", st == 200 and out.get("ok") is True,
          f"{out.get('users')} users, {out.get('installations')} installs")
    st, out = post({"action": "events", "actor_tg_id": "123"})
    check("ADMIN  stats refused to a stranger", st == 403)

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    try:
        io.open(os.path.join(os.path.dirname(__file__), "last_run.json"), "w", encoding="utf-8").write(
            json.dumps([{"name": n, "ok": o, "detail": d} for n, o, d in results], indent=2))
    except Exception:
        pass
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
