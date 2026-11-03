"""Garena host resolution — one place that knows which BD endpoints still resolve.

2026-11-03: the whole `ggpolarbear.com` apex went NXDOMAIN (Garena rotates these
domains every few weeks). Every hardcoded `loginbp.ggpolarbear.com` /
`clientbp.ggpolarbear.com` call started failing with "Name or service not known",
which the callers swallow as "no profile / no likes / login failed". That is why
/profile answered "Could not fetch profile" and likes delivered 0 while the pool
looked healthy.

Rules here:
  * always prefer the server_url the game itself hands back at MajorLogin
  * otherwise probe the known BD hosts once and cache the winner
  * never cache a host that does not resolve
"""
from __future__ import annotations

import socket
import time

# BD cluster hosts, best first. loginbp = auth, clientbp = game/profile/like.
# dinos: these are the BD (clientbp) cluster; a token only works on its own region.
BD_CLIENT_HOSTS = [
    "clientbp.ppmainecoonghj.com",
    "clientbp.ggblueshark.com",
    "clientbp.ggpolarbear.com",      # kept for the day it comes back
]
BD_LOGIN_HOSTS = [
    "loginbp.ppmainecoonghj.com",
    "loginbp.ggblueshark.com",
    "loginbp.ggpolarbear.com",
]

_CACHE: dict[str, tuple[float, str]] = {}
_TTL = 900  # 15 min: a host that dies mid-run is re-probed on the next call


def resolves(host: str) -> bool:
    try:
        socket.gethostbyname(host)
        return True
    except Exception:
        return False


def _pick(hosts: list[str], key: str) -> str:
    ts, host = _CACHE.get(key, (0.0, ""))
    if host and time.time() - ts < _TTL:
        return host
    for cand in hosts:
        if resolves(cand):
            _CACHE[key] = (time.time(), cand)
            return cand
    # nothing resolved: return the first so the error message names a real host
    return hosts[0]


def client_host() -> str:
    """Host for /GetPlayerPersonalShow, /LikeProfile, /GetClan* — no scheme."""
    return _pick(BD_CLIENT_HOSTS, "client")


def login_host() -> str:
    """Host for /MajorLogin, /MajorRegister — no scheme."""
    return _pick(BD_LOGIN_HOSTS, "login")


def client_base() -> str:
    return "https://" + client_host()


def login_base() -> str:
    return "https://" + login_host()


def normalise_server_url(url: str | None) -> str:
    """Turn whatever MajorLogin returned into a usable base URL.

    Returns "" when the value is empty, or a host we know is not the BD cluster
    (a US/EU token), so callers can refuse it instead of sending it to BD.
    """
    url = (url or "").strip().lower()
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    host = url.split("//", 1)[-1].split("/", 1)[0]
    if not host or _is_foreign(host):
        return ""
    return "https://" + host


_FOREIGN_HINTS = (".us.", ".eu.", ".sg.", ".id.", ".br.", ".ind.", ".ru.", ".pk.", ".th.", ".vn.", ".me.")


def _is_foreign(host: str) -> bool:
    return any(h in host for h in _FOREIGN_HINTS)


def is_bd_host(host_or_url: str) -> bool:
    """True when this host looks like the BD cluster (clientbp/loginbp, no region tag)."""
    h = (host_or_url or "").lower()
    if not h:
        return False
    if _is_foreign(h):
        return False
    return "bp." in h or h.startswith("clientbp") or h.startswith("loginbp")
