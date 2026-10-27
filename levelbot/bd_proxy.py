#!/usr/bin/env python3
"""Hunt a working BD proxy from guests_manager/proxies.txt.
Prints proxy URL (socks5:// or http://) with BD exit country, or nothing.
Used by run.sh when the BD openvpn tunnel is down — MajorLogin returns
"Protection Bypass" for BD-locked accounts from non-BD IPs.
"""
import sys, time
import httpx, warnings
warnings.filterwarnings("ignore")

PROXIES_FILE = "guests_manager/proxies.txt"
CHECK_URL = "https://ipinfo.io/json"
TIMEOUT = 8


def proxy_url(line):
    line = line.strip()
    if not line:
        return None
    port = line.rsplit(":", 1)[-1]
    if port in ("1080", "1081", "9050"):
        return f"socks5://{line}"
    return f"http://{line}"


def main():
    try:
        lines = open(PROXIES_FILE).read().splitlines()
    except Exception:
        return
    urls = [u for u in (proxy_url(l) for l in lines) if u]
    # most recently successful first: BD ranges are 103.x-heavy in this list, keep order
    for u in urls:
        try:
            r = httpx.get(CHECK_URL, proxy=u, timeout=TIMEOUT, verify=False)
            c = r.json().get("country")
            if c == "BD":
                print(u)
                return
        except Exception:
            continue
    # nothing found
    print("", end="")


if __name__ == "__main__":
    main()
