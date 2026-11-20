#!/usr/bin/env python3
"""
harvest.py — app-born guest harvest driver (runs on the CI runner).

For each requested guest the driver:
  1. clears the Free Fire app's data (fresh start),
  2. launches the app and attaches capture_guest.js with Frida,
  3. taps through the app's own guest-login flow (UI-dump based),
  4. waits until the script reports the guest the APP registered,
  5. records {uid, password} and repeats.

The app performs registration itself; this driver only navigates the UI
and collects the result. Output: harvest/guests_harvested.json merged
with whatever was harvested before (deduplicated by uid).
"""
import argparse, json, os, re, subprocess, sys, time

PKG_DEFAULT = "com.dts.freefireth"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "guests_harvested.json")
CAPTURE_JS = os.path.join(HERE, "capture_guest.js")


def adb(*args, timeout=60, check=False):
    r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def tap(x, y):
    adb("shell", "input", "tap", str(int(x)), str(int(y)))


def ui_dump():
    out = adb("exec-out", "uiautomator", "dump", "/dev/tty", timeout=30)
    return out or ""


def find_tap(xml, *words):
    """Return (x, y) of the first UI node whose text/desc contains a word."""
    for m in re.finditer(r'<node[^>]*?(?:text|content-desc)="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
        label = m.group(1).lower()
        if any(w.lower() in label for w in words):
            x = (int(m.group(2)) + int(m.group(4))) // 2
            y = (int(m.group(3)) + int(m.group(5))) // 2
            return x, y
    return None


def pull_captured(pkg):
    remote = f"/sdcard/Android/media/{pkg}/guest_accounts.json"
    local = os.path.join(HERE, "_captured.json")
    r = subprocess.run(["adb", "pull", remote, local], capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not os.path.exists(local):
        return []
    try:
        data = json.load(open(local))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def ensure_frida_server():
    ps = adb("shell", "ps", "-A")
    if "frida-server" in ps:
        return True
    adb("shell", "chmod", "755", "/data/local/tmp/frida-server")
    subprocess.Popen(["adb", "shell", "/data/local/tmp/frida-server", "&"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(15):
        time.sleep(1)
        if "frida-server" in adb("shell", "ps", "-A"):
            return True
    return False


def one_guest(frida_dev, pkg, proxy, timeout_s=240):
    before = {g.get("uid") for g in pull_captured(pkg)}
    adb("shell", "pm", "clear", pkg)
    if proxy:
        adb("shell", "settings", "put", "global", "http_proxy", proxy)
    adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
    # wait for the process, then attach
    pid = None
    for _ in range(60):
        pid_s = adb("shell", "pidof", pkg)
        if pid_s:
            pid = int(pid_s.split()[0])
            break
        time.sleep(2)
    if not pid:
        raise RuntimeError("app process never started")
    session = frida_dev.attach(pid)
    script = session.create_script(open(CAPTURE_JS).read())
    script.on("message", lambda m, d: print("[frida]", m.get("payload", m), flush=True))
    script.load()
    print(f"[harvest] attached to {pkg} pid={pid}; navigating to guest login", flush=True)

    deadline = time.time() + timeout_s
    tapped_guest = False
    while time.time() < deadline:
        now = pull_captured(pkg)
        fresh = [g for g in now if g.get("uid") and g.get("uid") not in before
                 and g.get("password") and "unknown" not in str(g.get("password"))]
        if fresh:
            g = fresh[-1]
            print(f"[harvest] captured uid={g['uid']}", flush=True)
            try:
                session.detach()
            except Exception:
                pass
            adb("shell", "am", "force-stop", pkg)
            return {"uid": str(g["uid"]), "password": str(g["password"])}
        xml = ui_dump()
        if not tapped_guest:
            pos = find_tap(xml, "guest")
            if pos:
                tap(*pos)
                tapped_guest = True
                print("[harvest] tapped the guest-login entry", flush=True)
                time.sleep(3)
                continue
        # dismiss common interstitials / continue prompts
        pos = find_tap(xml, "continue", "confirm", "ok", "start", "tap to start")
        if pos:
            tap(*pos)
        time.sleep(4)
    try:
        session.detach()
    except Exception:
        pass
    adb("shell", "am", "force-stop", pkg)
    raise RuntimeError("timed out waiting for a captured guest")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--pkg", default=PKG_DEFAULT)
    ap.add_argument("--proxy", default=os.environ.get("HARVEST_PROXY", ""))
    args = ap.parse_args()

    if not ensure_frida_server():
        sys.exit("frida-server is not running in the emulator (workflow step should have started it)")
    import frida
    dev = frida.get_usb_device(timeout=30)

    existing = []
    if os.path.exists(OUT):
        try:
            existing = json.load(open(OUT))
        except Exception:
            existing = []
    have = {g.get("uid") for g in existing}
    made = 0
    for i in range(args.count):
        try:
            g = one_guest(dev, args.pkg, args.proxy or None)
        except Exception as e:
            print(f"[harvest] cycle {i + 1} failed: {e}", flush=True)
            continue
        if g["uid"] not in have:
            existing.append(g)
            have.add(g["uid"])
            made += 1
        json.dump(existing, open(OUT, "w"), indent=1)
        print(f"[harvest] total harvested: {len(existing)} (new this run: {made})", flush=True)
        time.sleep(5)
    print(f"[harvest] done: {made} new guest(s), {len(existing)} total in {OUT}")
    if made == 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
