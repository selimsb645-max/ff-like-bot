#!/usr/bin/env python3
"""Local HTTP(S) proxy that forwards through one or more SOCKS5 hops.

    python3 chain_proxy.py 8899 127.0.0.1:1055 [100.x.x.x:1080 ...]

Why: the sandbox reaches the tailnet through tailscaled's userspace SOCKS5, and the
phone exposes microsocks inside that tailnet. One request therefore has to cross two
SOCKS5 servers in order, which no single HTTP library supports. This accepts a normal
HTTP CONNECT and walks the chain.
"""
import select
import socket
import struct
import sys
import threading


def socks5_connect(sock, host, port):
    sock.sendall(b"\x05\x01\x00")
    if sock.recv(2) != b"\x05\x00":
        raise OSError("socks hello refused")
    hb = host.encode()
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + struct.pack(">H", port))
    rep = sock.recv(4)
    if len(rep) < 2 or rep[1] != 0:
        raise OSError(f"socks connect failed ({rep[1] if len(rep) > 1 else '?'})")
    atyp = rep[3] if len(rep) > 3 else 1
    if atyp == 1:
        sock.recv(4 + 2)
    elif atyp == 3:
        n = sock.recv(1)[0]
        sock.recv(n + 2)
    else:
        sock.recv(16 + 2)


def dial(chain, host, port):
    """Open a socket to host:port through every hop in order."""
    first_host, first_port = chain[0]
    s = socket.create_connection((first_host, first_port), timeout=25)
    if len(chain) == 1:
        socks5_connect(s, host, port)
        return s
    socks5_connect(s, *chain[1])          # reach the next hop first
    for hop in chain[2:]:
        socks5_connect(s, *hop)
    socks5_connect(s, host, port)         # then the real destination
    return s


def pump(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 60)
            if not r:
                break
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


def handle(client, chain):
    try:
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = client.recv(4096)
            if not chunk:
                return
            head += chunk
        line = head.split(b"\r\n")[0].decode("latin1")
        method, target, _ = line.split(" ")
        if method.upper() != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Only CONNECT\r\n\r\n")
            client.close()
            return
        host, _, port = target.rpartition(":")
        remote = dial(chain, host.strip("[]"), int(port or 443))
        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        leftover = head.split(b"\r\n\r\n", 1)[1]
        if leftover:
            remote.sendall(leftover)
        pump(client, remote)
    except Exception as ex:
        try:
            client.sendall(f"HTTP/1.1 502 {type(ex).__name__}\r\n\r\n".encode())
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


def socks_front(client, chain):
    """Speak SOCKS5 to the client, then walk the chain to whatever it asked for.

    The game engine (levelbot/connection.py) only accepts a single socks5:// URL, so
    this turns the tailnet -> phone chain into one local SOCKS5 endpoint it can use.
    """
    try:
        head = client.recv(2)
        if len(head) < 2 or head[0] != 5:
            client.close(); return
        nm = client.recv(head[1]) if head[1] else b""
        client.sendall(b"\x05\x00")
        req = client.recv(4)
        if len(req) < 4:
            client.close(); return
        atyp = req[3]
        if atyp == 1:
            host = socket.inet_ntoa(client.recv(4))
        elif atyp == 3:
            n = client.recv(1)[0]
            host = client.recv(n).decode()
        else:
            host = socket.inet_ntop(socket.AF_INET6, client.recv(16))
        port = struct.unpack(">H", client.recv(2))[0]
        remote = dial(chain, host, port)
        client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack(">H", 0))
        pump(client, remote)
    except Exception:
        try:
            client.sendall(b"\x05\x01\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack(">H", 0))
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


def main():
    if sys.argv[1] == "--socks":
        port = int(sys.argv[2])
        chain = []
        for part in sys.argv[3:]:
            h, _, p = part.rpartition(":")
            chain.append((h, int(p)))
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(64)
        print(f"socks5 on 127.0.0.1:{port} via {' -> '.join(f'{h}:{p}' for h, p in chain)}", flush=True)
        while True:
            c, _ = srv.accept()
            threading.Thread(target=socks_front, args=(c, chain), daemon=True).start()

    port = int(sys.argv[1])
    chain = []
    for part in sys.argv[2:]:
        h, _, p = part.rpartition(":")
        chain.append((h, int(p)))
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(64)
    print(f"proxy on 127.0.0.1:{port} via {' -> '.join(f'{h}:{p}' for h, p in chain)}", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c, chain), daemon=True).start()


if __name__ == "__main__":
    # লাইসেন্স + লগইন গেট (guard/gate.py)
    from guard.gate import enforce as _enforce
    _enforce("chain_proxy.py")

    main()
