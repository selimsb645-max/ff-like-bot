#!/usr/bin/env python3
"""HTTP CHAIN — a local HTTP CONNECT proxy that tunnels through two SOCKS5 hops.

The phone's microsocks is only reachable through tailscaled's own SOCKS5, so a
plain proxy cannot reach it. httpx speaks HTTP CONNECT to an upstream proxy
reliably (the socksio path kept raising "Malformed reply"), so this exposes the
double hop as an HTTP proxy instead:

    python3 http_chain.py 8902 127.0.0.1:1055 100.112.148.12:1080
    LIKE_PROXY_URL=http://127.0.0.1:8902 ...

Anything sent through it leaves from the phone's own mobile/wifi IP.
"""
import select
import socket
import struct
import sys
import threading

BUF = 65536


def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise OSError("peer closed")
        data += chunk
    return data


def socks5_connect(sock, host, port, timeout=20):
    sock.settimeout(timeout)
    sock.sendall(b"\x05\x01\x00")
    if recv_exact(sock, 2) != b"\x05\x00":
        raise OSError("upstream refused no-auth")
    try:
        ip = socket.inet_aton(socket.gethostbyname(host))
        req = b"\x05\x01\x00\x01" + ip + struct.pack(">H", port)
    except OSError:
        name = host.encode()
        req = b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port)
    sock.sendall(req)
    head = recv_exact(sock, 4)
    if head[1] != 0:
        raise OSError(f"upstream connect failed code {head[1]}")
    if head[3] == 1:
        recv_exact(sock, 6)
    elif head[3] == 3:
        recv_exact(sock, recv_exact(sock, 1)[0] + 2)
    elif head[3] == 4:
        recv_exact(sock, 18)
    return sock


def pipe(a, b):
    socks = [a, b]
    try:
        while True:
            r, _, _ = select.select(socks, [], [], 180)
            if not r:
                return
            for s in r:
                data = s.recv(BUF)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def handle(client, chain):
    try:
        client.settimeout(30)
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = client.recv(4096)
            if not chunk:
                client.close()
                return
            raw += chunk
        line = raw.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            client.close()
            return
        host, _, port_s = parts[1].rpartition(":")
        port = int(port_s or 443)

        sock = socket.create_connection(chain[0], timeout=20)
        for nxt_host, nxt_port in chain[1:]:
            socks5_connect(sock, nxt_host, nxt_port)
        socks5_connect(sock, host, port)

        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        pipe(client, sock)
    except Exception:
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        except OSError:
            pass
        try:
            client.close()
        except OSError:
            pass


def main():
    port = int(sys.argv[1])
    chain = []
    for part in sys.argv[2:]:
        h, _, p = part.rpartition(":")
        chain.append((h, int(p)))
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(128)
    print(f"http chain on 127.0.0.1:{port} via {' -> '.join(f'{h}:{p}' for h, p in chain)}", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c, chain), daemon=True).start()


if __name__ == "__main__":
    main()
