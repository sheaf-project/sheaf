"""SSRF-safe HTTP helpers for outbound notification delivery.

Webhook + ntfy let owners enter arbitrary URLs. We must reject any URL whose
hostname resolves to a non-public address: RFC1918, loopback, link-local,
multicast, IMDS (169.254.169.254), CGN (100.64/10), or any IPv6 equivalent.

Validate-then-request is not enough on its own: an attacker's nameserver can
answer the validation lookup with a public address and the connection's
lookup with an internal one (DNS-rebinding TOCTOU). `resolve_pinned` closes
that window by resolving once, validating every returned address, and
rewriting the request to connect to the validated IP directly - the Host
header and TLS SNI/verification keep using the original hostname, so the
remote service sees a normal request but the socket cannot be steered
anywhere else. Resolution runs on the event loop's executor with a timeout
so a black-holed nameserver can't stall the dispatcher.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

import httpx

_DNS_TIMEOUT_SECONDS = 5.0


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


@dataclass
class PinnedRequest:
    """A validated outbound request spec, pinned to a resolved IP.

    `url` carries the IP in place of the hostname; `headers` carries the
    original Host; `extensions` carries sni_hostname for https so the TLS
    handshake (SNI + certificate verification) still uses the hostname.
    Merge all three into the httpx call.
    """

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    extensions: dict[str, str] = field(default_factory=dict)


async def resolve_pinned(url: str) -> PinnedRequest:
    """Resolve `url`'s host, validate every address, and pin the first.

    Raises SsrfRejected on a disallowed scheme/address, resolution
    failure, or resolution timeout. The returned spec connects to the
    validated IP, so a rebinding nameserver cannot redirect the actual
    connection after validation.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfRejected(f"scheme {parsed.scheme!r} not allowed")
    host = parsed.hostname
    if host is None:
        raise SsrfRejected("missing host")
    port = parsed.port

    # IP literal: validate and use as-is (nothing to rebind).
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is not None:
        if _is_disallowed(addr):
            raise SsrfRejected(f"address {host} is disallowed")
        return PinnedRequest(url=url)

    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(_DNS_TIMEOUT_SECONDS):
            infos = await loop.getaddrinfo(
                host, port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
    except TimeoutError as exc:
        raise SsrfRejected(f"DNS resolution timed out for {host}") from exc
    except socket.gaierror as exc:
        raise SsrfRejected(f"DNS resolution failed for {host}") from exc

    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            candidate = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_disallowed(candidate):
            raise SsrfRejected(
                f"{host} resolves to disallowed address {info[4][0]}"
            )
        addrs.append(candidate)
    if not addrs:
        raise SsrfRejected(f"DNS resolution returned no addresses for {host}")

    pinned = addrs[0]
    ip_str = (
        f"[{pinned}]" if isinstance(pinned, ipaddress.IPv6Address) else str(pinned)
    )
    # Preserve any userinfo the owner put in the URL (basic-auth webhooks).
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    netloc = f"{userinfo}{ip_str}" + (f":{port}" if port is not None else "")
    pinned_url = urlunparse(parsed._replace(netloc=netloc))

    host_header = host if port is None else f"{host}:{port}"
    extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}
    return PinnedRequest(
        url=pinned_url,
        headers={"Host": host_header},
        extensions=extensions,
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
