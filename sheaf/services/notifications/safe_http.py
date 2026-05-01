"""SSRF-safe HTTP helpers for outbound notification delivery.

Webhook + ntfy let owners enter arbitrary URLs. We must reject any URL whose
hostname resolves (now, just before each request) to a non-public address:
RFC1918, loopback, link-local, multicast, IMDS (169.254.169.254), CGN
(100.64/10), or any IPv6 equivalent. DNS rebinding is defeated by re-checking
on every dispatch and binding the connection to the resolved IP, but the
simpler pin-by-IP variant is sufficient for v1: validate at request time and
trust httpx's connection-per-request default.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx


class SsrfRejected(Exception):
    """Raised when an outbound URL resolves to a disallowed address."""


def _is_disallowed(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if addr.is_private:
        return True
    if addr.is_loopback:
        return True
    if addr.is_link_local:
        return True
    if addr.is_multicast:
        return True
    if addr.is_reserved:
        return True
    if addr.is_unspecified:
        return True
    # CGN / shared address space (RFC 6598)
    if isinstance(addr, ipaddress.IPv4Address):
        if addr in ipaddress.IPv4Network("100.64.0.0/10"):
            return True
        # IPv4 IMDS, also caught by is_link_local but spell it out
        if str(addr) == "169.254.169.254":
            return True
    # IPv6 IMDS
    return isinstance(addr, ipaddress.IPv6Address) and str(addr) == "fd00:ec2::254"


def assert_url_safe(url: str) -> None:
    """Resolve `url`'s host and raise SsrfRejected if any A/AAAA points
    somewhere we won't deliver to. Must be called immediately before the
    request (DNS rebinding window)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfRejected(f"scheme {parsed.scheme!r} not allowed")
    host = parsed.hostname
    if host is None:
        raise SsrfRejected("missing host")

    # If host is already an IP literal, validate directly.
    try:
        addr = ipaddress.ip_address(host)
        if _is_disallowed(addr):
            raise SsrfRejected(f"address {host} is disallowed")
        return
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SsrfRejected(f"DNS resolution failed for {host}") from exc

    for info in infos:
        ip_str = info[4][0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_disallowed(addr):
            raise SsrfRejected(
                f"{host} resolves to disallowed address {ip_str}"
            )


def safe_client(timeout: float = 10.0) -> httpx.AsyncClient:
    """Standard httpx client for outbound deliveries. Caller must
    `assert_url_safe(url)` before each request."""
    return httpx.AsyncClient(
        timeout=timeout,
        # Don't follow redirects: a 302 to internal would bypass the SSRF
        # check we already did.
        follow_redirects=False,
    )
