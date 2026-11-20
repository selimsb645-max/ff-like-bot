#!/usr/bin/env python3
"""HTTP proxy -> SOCKS5 upstream, for the harvest runner.

The Android emulator only accepts an HTTP proxy, while the phone's
microsocks speaks SOCKS5. This bridge listens on 127.0.0.1:8899 (the
emulator reaches it as http://10.0.2.2:8899) and forwards every
connection through the phone's SOCKS5 server, so the app's traffic
exits in BD exactly like the rest of the bot.

Usage: chain_proxy.py <phone_tailscale_ip> [socks_port] [listen_port]
"""
import select, socket, struct, sys, threading

PHONE = sys.argv[1] if len(sys.argv) > 1 else "100.112.148.12"
SOCKS_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 1080
LISTEN = int(sys.argv[3]) if len(sys.argv) > 3 else 8899


def socks_connect(host, port):
    s = socket.create_connection((PHONE, SOCKS_PORT), timeout=30)
    s.sendall(b"\x05\x01\x00")
    if s.recv(2) != b"\x05\x00":
        s.close(); raise OSError("socks greeting refused")
    hb = host.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + struct.pack(">H", port))
    rep = s.recv(4)
    if len(rep) < 2 or rep[1] != 0:
        s.close(); raise OSError("socks connect refused")
    atyp = rep[3]
    if atyp == 1: s.recv(6)
    elif atyp == 3: s.recv(s.recv(1)[0] + 2)
    elif atyp == 4: s.recv(18)
    return s


def pump(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 300)
            if not r:
                break
            for src in r:
                data = src.recv(65536)
                if not data:
                    return
                (b if src is a else a).sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try: s.close()
            except OSError: pass


def handle(client):
    try:
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = client.recv(4096)
            if not chunk:
                client.close(); return
            buf += chunk
        line = buf.split(b"\r\n")[0].decode("latin1")
        parts = line.split(" ")
        method, target = parts[0].upper(), parts[1]
        if method == "CONNECT":
            host, _, port = target.partition(":")
            up = socks_connect(host, int(port or 443))
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            rest = buf.split(b"\r\n\r\n", 1)[1]
            if rest: up.sendall(rest)
        else:
            from urllib.parse import urlparse
            u = urlparse(target)
            host, port = u.hostname, u.port or 80
            up = socks_connect(host, port)
            up.sendall(buf)
        pump(client, up)
    except Exception:
        try: client.close()
        except OSError: pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", LISTEN))
    srv.listen(64)
    print(f"chain proxy on :{LISTEN} via phone {PHONE}:{SOCKS_PORT}", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
