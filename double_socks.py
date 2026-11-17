#!/usr/bin/env python3
"""DOUBLE SOCKS — one local SOCKS5 that chains two upstream SOCKS5 hops.

The phone sits behind Tailscale, and Tailscale here runs userspace, so the only
way to reach the phone's microsocks is through tailscaled's own SOCKS5. That makes
every outbound request a hop inside a hop, which httpx cannot do by itself.

    python3 double_socks.py 8899 127.0.0.1:1055 100.112.148.12:1080

Then anything pointed at socks5://127.0.0.1:8899 leaves from the phone's IP.
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
            raise OSError("peer closed during handshake")
        data += chunk
    return data


def socks5_connect(sock, host, port, timeout=20):
    """Perform a SOCKS5 handshake on an already-connected socket."""
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
        raise OSError(f"upstream connect failed (code {head[1]})")
    atyp = head[3]
    if atyp == 1:
        recv_exact(sock, 4 + 2)
    elif atyp == 3:
        ln = recv_exact(sock, 1)[0]
        recv_exact(sock, ln + 2)
    elif atyp == 4:
        recv_exact(sock, 16 + 2)
    return sock


def pipe(a, b):
    socks = [a, b]
    try:
        while True:
            r, _, _ = select.select(socks, [], [], 120)
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
        client.settimeout(20)
        head = recv_exact(client, 2)
        if head[0] != 5:
            client.close()
            return
        n = head[1]
        recv_exact(client, n)                      # methods offered
        client.sendall(b"\x05\x00")

        ver, cmd, _, atyp = recv_exact(client, 4)
        if atyp == 1:
            host = socket.inet_ntoa(recv_exact(client, 4))
        elif atyp == 3:
            ln = recv_exact(client, 1)[0]
            host = recv_exact(client, ln).decode()
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, recv_exact(client, 16))
        else:
            client.close()
            return
        port = struct.unpack(">H", recv_exact(client, 2))[0]

        # dial the first hop directly, then tunnel each further hop through the
        # previous one, and finally the real destination through the last hop
        sock = socket.create_connection(chain[0], timeout=20)
        for nxt_host, nxt_port in chain[1:]:
            socks5_connect(sock, nxt_host, nxt_port)
        socks5_connect(sock, host, port)
        upstream = sock

        client.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        pipe(client, upstream)
    except Exception:
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
    srv.listen(64)
    print(f"double socks on 127.0.0.1:{port} via {' -> '.join(f'{h}:{p}' for h, p in chain)}", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c, chain), daemon=True).start()


if __name__ == "__main__":
    # লাইসেন্স + লগইন গেট (guard/gate.py)
    from guard.gate import enforce as _enforce
    _enforce("double_socks.py")

    main()
