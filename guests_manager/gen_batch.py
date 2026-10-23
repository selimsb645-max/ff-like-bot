import guest_gen, time, sys
n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
ok = 0
for i in range(n):
    try:
        a = guest_gen.create_guest()
        ok += 1
        print(f"[{i+1}/{n}] OK uid={a['uid']}", flush=True)
    except Exception as e:
        print(f"[{i+1}/{n}] FAIL {e}", flush=True)
    if i < n - 1:
        time.sleep(70)
print(f"done: {ok}/{n}", flush=True)
