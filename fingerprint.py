#!/usr/bin/env python3
"""Print a fingerprint of the match-critical parts of app.py.

Run this on BOTH copies (yours and the friend's working one) and compare the
numbers. Any differing hash = that function is where the real logic lives.

    python3 fingerprint.py            # uses ./app.py
    python3 fingerprint.py /path/app.py
"""
import ast
import hashlib
import sys

WANT = [
    "functional_lone_wolf",
    "informational",
    "accept_team_invite",
    "invite_player_in_team",
    "team_fresh",
    "start_game_battle_royale",
    "build_tcp_startup_packet",
    "reply_for",
    "build_hello_packet",
    "send_keep_alive",
    "create_four_accounts",
    "join_clan",
]

path = sys.argv[1] if len(sys.argv) > 1 else "app.py"
src = open(path, "r", encoding="utf-8", errors="replace").read()
print(f"file      : {path}")
print(f"size      : {len(src)} bytes")
print(f"sha256    : {hashlib.sha256(src.encode()).hexdigest()[:32]}")
try:
    tree = ast.parse(src)
except SyntaxError as e:
    print("cannot parse:", e)
    raise SystemExit(1)

lines = src.splitlines()
seen = set()
print(f"\n{'function':<26}{'lines':>7}{'bytes':>8}  sha256[:16]")
print("-" * 68)
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in WANT:
        body = "\n".join(lines[node.lineno - 1:node.end_lineno])
        h = hashlib.sha256(body.encode()).hexdigest()[:16]
        seen.add(node.name)
        print(f"{node.name:<26}{node.end_lineno - node.lineno + 1:>7}{len(body):>8}  {h}")
missing = [w for w in WANT if w not in seen]
if missing:
    print("\nNOT PRESENT in this file:", ", ".join(missing))
print("\nAlso check these constants:")
for k in ("TEAM_MODE", "TEAM_SIZE", "MATCH_IDLE_TIMEOUT", "MAX_MATCH_DURATION"):
    for ln in lines:
        if ln.startswith(k):
            print("  ", ln.strip())
