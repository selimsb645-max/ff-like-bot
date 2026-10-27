#!/usr/bin/env python3
"""Hunt a BD proxy that BOTH exits in BD AND can reach Garena MSDK over TLS.
Skips socks4 (PySocks hangs). Prints the proxy URL on the last line, or nothing."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "guests_manager"))
import urllib3
urllib3.disable_warnings()
import httpx
import game_register as gr
from concurrent.futures import ThreadPoolExecutor

cands = gr.proxy_candidates()
with ThreadPoolExecutor(max_workers=12) as ex:
    alive = list(ex.map(gr.tcp_alive, cands))
cands = [c for c, a in zip(cands, alive) if a]
for url, ip, country in gr.ex_map_results(cands):
    if not url or country != "BD" or url.startswith("socks4"):
        continue
    try:
        httpx.get("https://ffmconnect.live.gop.garenanow.com", proxy=url, timeout=8, verify=False)
    except Exception:
        continue
    print(url)
    break
