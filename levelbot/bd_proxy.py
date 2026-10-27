#!/usr/bin/env python3
"""Hunt a BD proxy using game_register's battle-tested hunt
(static proxies.txt + live proxy sources, verified BD exit country).
Prints the proxy URL on the last line, or nothing if none found."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "guests_manager"))
import urllib3
urllib3.disable_warnings()
import game_register as gr

url = gr.pick_proxy()
if url:
    print(url)
