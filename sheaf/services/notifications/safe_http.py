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

from sheaf.config import settings

_DNS_TIMEOUT_SECONDS = 5.0

# CGN / shared address space (RFC 6598).
_CGN_NETWORK = ipaddress.IPv4Network("100.64.0.0/10")


class SsrfRejected(Exception):
    """Raised when an outbound URL resolves to a disallowed address."""


class SsrfResolutionError(SsrfRejected):
    """Raised when an outbound URL's host could not be resolved (DNS failure,
    timeout, or an empty answer) - a transient condition, distinct from a
    definitive disallowed-scheme / disallowed-address rejection. Subclasses
    SsrfRejected so delivery-time handlers (which fail the whole batch on any
    SsrfRejected) are unchanged; callers that must tell "try again later" from
    "never deliver here" - e.g. config-time validation - catch this first."""


def _is_hard_never(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Addresses that are NEVER deliverable, even if an operator's allowlist
    CIDR would cover them: cloud metadata (hands out cloud credentials, no
    webhook legitimately targets it), the unspecified address, and multicast."""
    if str(addr) in ("169.254.169.254", "fd00:ec2::254"):
        return True
    return addr.is_unspecified or addr.is_multicast


def _default_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """The default deny set an operator allowlist may override: private
    (RFC1918 / ULA), loopback, link-local, reserved, and CGN."""
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        return True
    return isinstance(addr, ipaddress.IPv4Address) and addr in _CGN_NETWORK


def _allowlisted(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if `addr` falls in an operator-permitted private range. The
    property is [] in SaaS mode, so this is always False there."""
    return any(addr in net for net in settings.webhook_allowed_private_networks)


def allowlist_override_applies(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """True when `addr` is deliverable ONLY because the operator allowlist
    permits an otherwise-blocked private range. The signal for the
    "self-host opt-in was actually exercised" metric: excludes public
    addresses (allowed regardless) and hard-never addresses (never allowed)."""
    return (
        not _is_hard_never(addr) and _allowlisted(addr) and _default_blocked(addr)
    )


def _is_disallowed(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Hard-never: cloud metadata + unspecified + multicast are NEVER allowed,
    # even if an operator's allowlist CIDR would cover them.
    if _is_hard_never(addr):
        return True
    # Operator opt-in (self-host only; the property is [] in SaaS mode): an
    # explicitly-allowed private range overrides the default blocks below.
    if _allowlisted(addr):
        return False
    # Default deny set: private, loopback, link-local, reserved, CGN.
    return _default_blocked(addr)


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
    # True when the pinned address was deliverable only because it fell in an
    # operator-permitted private range (WEBHOOK_ALLOWED_PRIVATE_CIDRS). Set once
    # per resolution so the delivery site can count the opt-in without
    # double-counting per resolved IP.
    allowlisted_private: bool = False


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
        return PinnedRequest(
            url=url, allowlisted_private=allowlist_override_applies(addr)
        )

    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(_DNS_TIMEOUT_SECONDS):
            infos = await loop.getaddrinfo(
                host, port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
    except TimeoutError as exc:
        raise SsrfResolutionError(f"DNS resolution timed out for {host}") from exc
    except socket.gaierror as exc:
        raise SsrfResolutionError(f"DNS resolution failed for {host}") from exc

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
        raise SsrfResolutionError(
            f"DNS resolution returned no addresses for {host}"
        )

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
        allowlisted_private=allowlist_override_applies(pinned),
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


# ---------------------------------------------------------------------------
# Sync / requests pinning (pywebpush)
# ---------------------------------------------------------------------------
#
# httpx pinning above can't help pywebpush: it is sync (requests under the
# hood) and opens its own connection, re-resolving the endpoint host and
# reopening the DNS-rebinding window that resolve_pinned closes for webhook
# and ntfy. These helpers give the same guarantee for the requests path -
# resolve + validate once, then hand pywebpush a session whose connections
# are pinned to the validated IP (Host header + TLS SNI/verification stay on
# the original hostname). Both run under asyncio.to_thread since getaddrinfo
# and requests are blocking.


def resolve_pinned_ip(url: str) -> tuple[str, str]:
    """Resolve `url`'s host, validate every address, return (host, ip_literal).

    Sync counterpart to resolve_pinned for the requests/pywebpush path. The
    returned ip_literal is netloc-ready (IPv6 already bracketed) for pinning
    the connection target. Raises SsrfRejected on a disallowed scheme or any
    disallowed / unresolvable address. Validates every returned address (not
    just the pinned one) so a mixed public+internal answer is rejected
    outright.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfRejected(f"scheme {parsed.scheme!r} not allowed")
    host = parsed.hostname
    if host is None:
        raise SsrfRejected("missing host")

    # IP literal: validate and use as-is (nothing to rebind).
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is not None:
        if _is_disallowed(addr):
            raise SsrfRejected(f"address {host} is disallowed")
        lit = f"[{addr}]" if isinstance(addr, ipaddress.IPv6Address) else str(addr)
        return host, lit

    try:
        infos = socket.getaddrinfo(
            host, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise SsrfResolutionError(f"DNS resolution failed for {host}") from exc

    chosen: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    for info in infos:
        try:
            candidate = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_disallowed(candidate):
            raise SsrfRejected(
                f"{host} resolves to disallowed address {info[4][0]}"
            )
        if chosen is None:
            chosen = candidate
    if chosen is None:
        raise SsrfResolutionError(
            f"DNS resolution returned no addresses for {host}"
        )

    lit = f"[{chosen}]" if isinstance(chosen, ipaddress.IPv6Address) else str(chosen)
    return host, lit


# Cached so we import requests (transitive via pywebpush, optional in minimal
# deploys) lazily and build the adapter class only once.
_pinned_adapter_cls = None


def _get_pinned_adapter_cls():  # noqa: ANN202 - returns a requests.HTTPAdapter subclass
    global _pinned_adapter_cls
    if _pinned_adapter_cls is not None:
        return _pinned_adapter_cls

    import requests

    class _PinnedIPAdapter(requests.adapters.HTTPAdapter):
        """Force every connection to a pre-validated IP while keeping the
        original Host header and TLS hostname (SNI + cert verification).

        Merges the two requests-toolbelt recipes (ForcedIP + HostHeaderSSL):
        rewrite the connection target to the pinned IP, but verify the cert
        against the real hostname so a rebind can't downgrade TLS either.
        """

        def __init__(self, host: str, ip_literal: str, **kwargs) -> None:
            self._pin_host = host
            self._pin_ip = ip_literal
            super().__init__(**kwargs)

        def send(self, request, **kwargs):  # noqa: ANN001, ANN002, ANN003
            parsed = urlparse(request.url)
            if parsed.hostname != self._pin_host:
                # A redirect (or unexpected reuse) to a different host must not
                # ride the pin; the pinned IP belongs to _pin_host only.
                raise SsrfRejected(
                    f"request host {parsed.hostname!r} does not match pinned "
                    f"host {self._pin_host!r}"
                )
            host_header = self._pin_host
            if parsed.port is not None:
                host_header = f"{host_header}:{parsed.port}"
            request.headers["Host"] = host_header
            if parsed.scheme == "https":
                # SNI + hostname verification stay on the real host even though
                # we connect to the IP.
                self.poolmanager.connection_pool_kw["server_hostname"] = self._pin_host
                self.poolmanager.connection_pool_kw["assert_hostname"] = self._pin_host
            netloc = self._pin_ip
            if parsed.port is not None:
                netloc = f"{netloc}:{parsed.port}"
            request.url = urlunparse(parsed._replace(netloc=netloc))
            return super().send(request, **kwargs)

    _pinned_adapter_cls = _PinnedIPAdapter
    return _pinned_adapter_cls


def pinned_requests_session(url: str):  # noqa: ANN201 - returns a requests.Session
    """Build a requests.Session pinned to `url`'s validated public IP.

    Resolves + validates once, then mounts an adapter that steers every
    connection to that IP with the Host header and TLS hostname preserved.
    Redirect-following is disabled: a 3xx to an internal host would be
    re-resolved by requests and dodge the pin. Raises SsrfRejected. Intended
    to be handed to pywebpush via requests_session=. Run under
    asyncio.to_thread - resolution and the eventual request both block.
    """
    import requests

    host, ip_literal = resolve_pinned_ip(url)
    session = requests.Session()
    # First redirect raises TooManyRedirects rather than being followed.
    session.max_redirects = 0
    adapter = _get_pinned_adapter_cls()(host, ip_literal)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
