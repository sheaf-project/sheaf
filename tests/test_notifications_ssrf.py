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


# ---------------------------------------------------------------------------
# Sync pinning for pywebpush: resolve_pinned_ip + the pinned requests session


def _stub_sync_resolution(monkeypatch, addresses: list[str]):
    """Shadow the module's socket.getaddrinfo with a canned answer."""
    import socket as _socket

    from sheaf.services.notifications import safe_http

    def fake_getaddrinfo(host, port, **kwargs):
        family = _socket.AF_INET6 if ":" in addresses[0] else _socket.AF_INET
        return [
            (family, _socket.SOCK_STREAM, 6, "", (addr, port or 0))
            for addr in addresses
        ]

    monkeypatch.setattr(safe_http.socket, "getaddrinfo", fake_getaddrinfo)


def test_resolve_pinned_ip_returns_validated_ip(monkeypatch):
    from sheaf.services.notifications.safe_http import resolve_pinned_ip

    _stub_sync_resolution(monkeypatch, ["93.184.216.34"])
    host, ip = resolve_pinned_ip("https://push.example.com/ep")
    assert host == "push.example.com"
    assert ip == "93.184.216.34"


def test_resolve_pinned_ip_brackets_ipv6(monkeypatch):
    from sheaf.services.notifications.safe_http import resolve_pinned_ip

    _stub_sync_resolution(monkeypatch, ["2606:4700:4700::1111"])
    host, ip = resolve_pinned_ip("https://push.example.com/ep")
    assert ip == "[2606:4700:4700::1111]"


def test_resolve_pinned_ip_rejects_rebind_to_private(monkeypatch):
    from sheaf.services.notifications.safe_http import resolve_pinned_ip

    _stub_sync_resolution(monkeypatch, ["10.0.0.5"])
    with pytest.raises(SsrfRejected):
        resolve_pinned_ip("https://push.example.com/ep")


def test_resolve_pinned_ip_rejects_mixed_answers(monkeypatch):
    from sheaf.services.notifications.safe_http import resolve_pinned_ip

    _stub_sync_resolution(monkeypatch, ["93.184.216.34", "169.254.169.254"])
    with pytest.raises(SsrfRejected):
        resolve_pinned_ip("https://push.example.com/ep")


def test_resolve_pinned_ip_rejects_ip_literal_loopback():
    from sheaf.services.notifications.safe_http import resolve_pinned_ip

    with pytest.raises(SsrfRejected):
        resolve_pinned_ip("https://127.0.0.1/ep")


def test_resolve_pinned_ip_rejects_bad_scheme():
    from sheaf.services.notifications.safe_http import resolve_pinned_ip

    with pytest.raises(SsrfRejected):
        resolve_pinned_ip("ftp://push.example.com/ep")


def test_pinned_session_rewrites_connection_to_ip_and_pins_tls(monkeypatch):
    """The adapter steers the socket to the validated IP while keeping the
    Host header and TLS hostname on the real host, and blocks redirects."""
    import requests

    from sheaf.services.notifications.safe_http import pinned_requests_session

    _stub_sync_resolution(monkeypatch, ["93.184.216.34"])
    session = pinned_requests_session("https://push.example.com/ep")
    # Redirect-following is disabled so a 3xx can't dodge the pin.
    assert session.max_redirects == 0

    adapter = session.get_adapter("https://push.example.com/ep")

    captured: dict = {}

    def fake_super_send(self, request, **kwargs):  # noqa: ANN001
        captured["url"] = request.url
        captured["host"] = request.headers.get("Host")
        return "sentinel"

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_super_send)

    req = requests.Request("POST", "https://push.example.com/ep", data=b"x").prepare()
    result = adapter.send(req)

    assert result == "sentinel"
    # Connection target rewritten to the validated IP...
    assert captured["url"] == "https://93.184.216.34/ep"
    # ...but Host + TLS verification stay on the original hostname.
    assert captured["host"] == "push.example.com"
    assert adapter.poolmanager.connection_pool_kw["server_hostname"] == "push.example.com"
    assert adapter.poolmanager.connection_pool_kw["assert_hostname"] == "push.example.com"


def test_pinned_session_rejects_host_mismatch(monkeypatch):
    """A request (e.g. a redirect) to a host other than the pinned one must
    not ride the pin."""
    import requests

    from sheaf.services.notifications.safe_http import pinned_requests_session

    _stub_sync_resolution(monkeypatch, ["93.184.216.34"])
    session = pinned_requests_session("https://push.example.com/ep")
    adapter = session.get_adapter("https://push.example.com/ep")

    req = requests.Request("POST", "https://evil.example.net/ep").prepare()
    with pytest.raises(SsrfRejected):
        adapter.send(req)
