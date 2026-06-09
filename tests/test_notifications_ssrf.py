"""SSRF guard for outbound webhook + ntfy URLs.

Pure-Python checks against `assert_url_safe`. Network is not actually
contacted; we only verify the resolver/classifier rejects disallowed
destinations.
"""

from __future__ import annotations

import pytest

from sheaf.services.notifications.safe_http import (
    SsrfRejected,
    assert_url_safe,
    resolve_pinned,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/foo",
        "http://[::1]/foo",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://100.64.0.1/",  # CGN
        "http://0.0.0.0/",
        "ftp://example.com/x",  # bad scheme
    ],
)
def test_disallowed_urls_rejected(url: str):
    with pytest.raises(SsrfRejected):
        assert_url_safe(url)


def test_public_url_accepted():
    # example.com resolves to a public unicast IP. If your CI has DNS
    # filtering that resolves it to RFC1918, this test will (correctly) fail.
    assert_url_safe("https://example.com/webhook")


def test_missing_host_rejected():
    with pytest.raises(SsrfRejected):
        assert_url_safe("https:///foo")


# ---------------------------------------------------------------------------
# resolve_pinned: the connection goes to the address that was validated


def _stub_loop_resolution(addresses: list[str]):
    """Shadow the running loop's getaddrinfo with a canned answer."""
    import asyncio
    import socket as _socket

    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, **kwargs):
        family = _socket.AF_INET6 if ":" in addresses[0] else _socket.AF_INET
        return [
            (family, _socket.SOCK_STREAM, 6, "", (addr, port or 0))
            for addr in addresses
        ]

    loop.getaddrinfo = fake_getaddrinfo  # instance attr shadows the method


def test_resolve_pinned_rewrites_host_to_validated_ip():
    import asyncio

    async def run():
        _stub_loop_resolution(["93.184.216.34"])
        pinned = await resolve_pinned("https://hooks.example.com/notify")
        assert pinned.url == "https://93.184.216.34/notify"
        assert pinned.headers == {"Host": "hooks.example.com"}
        assert pinned.extensions == {"sni_hostname": "hooks.example.com"}

    asyncio.run(run())


def test_resolve_pinned_keeps_port_and_plain_http_has_no_sni():
    import asyncio

    async def run():
        _stub_loop_resolution(["93.184.216.34"])
        pinned = await resolve_pinned("http://hooks.example.com:8080/notify")
        assert pinned.url == "http://93.184.216.34:8080/notify"
        assert pinned.headers == {"Host": "hooks.example.com:8080"}
        assert pinned.extensions == {}

    asyncio.run(run())


def test_resolve_pinned_brackets_ipv6():
    import asyncio

    async def run():
        _stub_loop_resolution(["2606:4700:4700::1111"])
        pinned = await resolve_pinned("https://hooks.example.com/x")
        assert pinned.url == "https://[2606:4700:4700::1111]/x"

    asyncio.run(run())


def test_resolve_pinned_rejects_rebind_to_private():
    """The rebinding shape: the nameserver answers with an internal
    address at request time. Validation and connection use the same
    lookup, so this is caught."""
    import asyncio

    import pytest as _pytest

    async def run():
        _stub_loop_resolution(["10.0.0.5"])
        with _pytest.raises(SsrfRejected):
            await resolve_pinned("https://hooks.example.com/x")

    asyncio.run(run())


def test_resolve_pinned_rejects_mixed_public_private_answers():
    import asyncio

    import pytest as _pytest

    async def run():
        _stub_loop_resolution(["93.184.216.34", "169.254.169.254"])
        with _pytest.raises(SsrfRejected):
            await resolve_pinned("https://hooks.example.com/x")

    asyncio.run(run())


def test_resolve_pinned_ip_literal_passes_through_unchanged():
    import asyncio

    async def run():
        pinned = await resolve_pinned("https://93.184.216.34/x")
        assert pinned.url == "https://93.184.216.34/x"
        assert pinned.headers == {}
        assert pinned.extensions == {}

    asyncio.run(run())


def test_resolve_pinned_rejects_loopback_literal():
    import asyncio

    import pytest as _pytest

    async def run():
        with _pytest.raises(SsrfRejected):
            await resolve_pinned("http://127.0.0.1/x")

    asyncio.run(run())
