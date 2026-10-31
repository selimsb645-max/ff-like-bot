#!/usr/bin/env python3
"""Is the MATCH server reachable from this phone?

The bot finds a match, joins it, and then sits silent because the match server
never answers. This tells you whether that is a network problem (UDP blocked on
your mobile data) or a packet problem (our join packet is wrong).

    python3 netcheck.py <match_ip:port>        # e.g. 202.81.106.76:10018

Run it WHILE the bot log shows "Match #N Injected -> ip:port".
"""
import socket
import sys
import time


def probe_udp(host, port, payload=b"\x12\x00", tries=5, wait=2.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(wait)
    got = 0
    for i in range(tries):
        try:
            s.sendto(payload, (host, port))
            data, addr = s.recvfrom(4096)
            got += 1
            print(f"  udp #{i+1}: REPLY {len(data)}B from {addr} head={data[:24].hex()}")
        except socket.timeout:
            print(f"  udp #{i+1}: no reply ({wait:.0f}s)")
        except Exception as e:
            print(f"  udp #{i+1}: {type(e).__name__}: {e}")
        time.sleep(0.4)
    s.close()
    return got


def probe_tcp(host, port, wait=6.0):
    try:
        s = socket.create_connection((host, port), timeout=wait)
        print(f"  tcp: CONNECTED {s.getpeername()}")
        s.close()
        return True
    except Exception as e:
        print(f"  tcp: FAIL {type(e).__name__}: {e}")
        return False


def main():
    if len(sys.argv) < 2 or ":" not in sys.argv[1]:
        print(__doc__)
        return
    host, port = sys.argv[1].split(":")
    port = int(port)
    print(f"target {host}:{port}")
    tcp_ok = probe_tcp(host, port)
    replies = probe_udp(host, port)
    print("\nVERDICT")
    if replies:
        print("  UDP replies received -> network is fine, our in-match packet is wrong.")
    elif tcp_ok:
        print("  TCP opens but UDP gets nothing -> the server ignores our packets;")
        print("  the join/ready packet needs fixing (not the network).")
    else:
        print("  Neither TCP nor UDP answers -> this network path is blocked.")
        print("  Try mobile data vs Wi-Fi, or a VPN, then re-run.")


if __name__ == "__main__":
    main()
