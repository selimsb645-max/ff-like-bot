#!/usr/bin/env python3
"""Why does the mint fail on this phone when TCP is fine?

    python3 diag_phone.py

Runs the SAME request through four different clients - raw TLS, urllib, requests
and httpx - against the grant endpoint. Whichever one works tells us where the
break is: the phone network, the TLS stack, or httpx specifically.
"""
import json
import socket
import ssl
import sys
import time
import urllib.request

HOSTS = ["ffmconnect.live.gop.garenanow.com", "100067.connect.garena.com"]
PATH = "/api/v2/oauth/guest/token:grant"
BODY = json.dumps({"client_id": 100067, "client_secret": "x", "client_type": 2,
                   "password": "x", "response_type": "token", "uid": 1}).encode()
UA = "GarenaMSDK/4.0.19P10(I2404 ;Android 15;en;US;)"


def line(msg):
    print(msg, flush=True)


def raw_tls(host):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    t0 = time.time()
    try:
        with socket.create_connection((host, 443), timeout=10) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ts:
                line(f"  raw TLS      ok  {ts.version()} {ts.cipher()[0]}  {time.time()-t0:.1f}s")
                return True
    except Exception as ex:
        line(f"  raw TLS      FAIL {type(ex).__name__}: {ex}  {time.time()-t0:.1f}s")
        return False


def via_urllib(host):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request("https://" + host + PATH, data=BODY,
                                 headers={"User-Agent": UA,
                                          "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            line(f"  urllib       http{r.status}  {time.time()-t0:.1f}s")
    except urllib.error.HTTPError as ex:
        line(f"  urllib       http{ex.code} (endpoint answered)  {time.time()-t0:.1f}s")
    except Exception as ex:
        line(f"  urllib       FAIL {type(ex).__name__}: {ex}  {time.time()-t0:.1f}s")


def via_requests(host):
    try:
        import requests
    except Exception:
        line("  requests     not installed")
        return
    t0 = time.time()
    try:
        r = requests.post("https://" + host + PATH, data=BODY,
                          headers={"User-Agent": UA, "Content-Type": "application/json"},
                          timeout=15, verify=False)
        line(f"  requests     http{r.status_code} (endpoint answered)  {time.time()-t0:.1f}s")
    except Exception as ex:
        line(f"  requests     FAIL {type(ex).__name__}: {ex}  {time.time()-t0:.1f}s")


def via_httpx(host):
    try:
        import httpx
    except Exception:
        line("  httpx        not installed")
        return
    t0 = time.time()
    try:
        with httpx.Client(verify=False, http2=False, trust_env=False,
                          follow_redirects=False, timeout=15) as c:
            r = c.post("https://" + host + PATH, content=BODY,
                       headers={"User-Agent": UA, "Content-Type": "application/json"})
        line(f"  httpx        http{r.status_code} (endpoint answered)  {time.time()-t0:.1f}s")
    except Exception as ex:
        line(f"  httpx        FAIL {type(ex).__name__}: {ex}  {time.time()-t0:.1f}s")


def main():
    line(f"python {sys.version.split()[0]} on this device")
    for host in HOSTS:
        try:
            ip = socket.gethostbyname(host)
        except Exception as ex:
            line(f"{host}: DNS FAILED {ex}")
            continue
        line(f"\n{host}  ->  {ip}")
        if not raw_tls(host):
            continue
        via_urllib(host)
        via_requests(host)
        via_httpx(host)
    line("\nSend this whole output back. If raw TLS + urllib answer but httpx shows")
    line("ConnectError, the phone is fine and only the httpx path needs changing.")
    return 0


if __name__ == "__main__":
    # লাইসেন্স + লগইন গেট (guard/gate.py)
    from guard.gate import enforce as _enforce
    _enforce("diag_phone.py")

    sys.exit(main())
