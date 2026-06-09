"""Unit tests for client_ip() and TRUSTED_PROXIES CIDR parsing.

Runs in-process; does not use the test server fixture.
"""

from unittest.mock import patch

import pytest
from fastapi import Request

from sheaf import request as request_module
from sheaf.config import Settings


def _make_request(direct_ip: str, xff: str | None = None) -> Request:
    """Build a minimal Starlette Request with a given peer IP and optional XFF."""
    headers: list[tuple[bytes, bytes]] = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (direct_ip, 12345),
    }
    return Request(scope)


def _run_client_ip(trusted_proxies: str, direct_ip: str, xff: str | None) -> str:
    """Invoke client_ip() with a patched settings.trusted_proxies."""
    patched = Settings(trusted_proxies=trusted_proxies)
    with patch.object(request_module, "settings", patched):
        return request_module.client_ip(_make_request(direct_ip, xff))


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def test_trusted_proxies_empty_means_no_networks():
    s = Settings(trusted_proxies="")
    assert s.trusted_proxy_networks == []


def test_trusted_proxies_parses_bare_ip():
    s = Settings(trusted_proxies="127.0.0.1")
    nets = s.trusted_proxy_networks
    assert len(nets) == 1
    assert str(nets[0]) == "127.0.0.1/32"


def test_trusted_proxies_parses_cidr():
    s = Settings(trusted_proxies="172.16.0.0/12")
    nets = s.trusted_proxy_networks
    assert len(nets) == 1
    assert str(nets[0]) == "172.16.0.0/12"


def test_trusted_proxies_mixed_list():
    s = Settings(trusted_proxies="127.0.0.1, 172.16.0.0/12, ::1")
    nets = s.trusted_proxy_networks
    assert len(nets) == 3
    assert {str(n) for n in nets} == {"127.0.0.1/32", "172.16.0.0/12", "::1/128"}


def test_trusted_proxies_invalid_entry_raises_at_startup():
    with pytest.raises(ValueError, match="Invalid entry in TRUSTED_PROXIES"):
        Settings(trusted_proxies="127.0.0.1,not-an-ip")


def test_trusted_proxies_invalid_cidr_raises():
    with pytest.raises(ValueError, match="Invalid entry in TRUSTED_PROXIES"):
        Settings(trusted_proxies="10.0.0.0/99")


# ---------------------------------------------------------------------------
# client_ip() behaviour
# ---------------------------------------------------------------------------

def test_no_xff_returns_direct_ip():
    assert _run_client_ip("127.0.0.1", "127.0.0.1", None) == "127.0.0.1"


def test_empty_trusted_proxies_never_reads_xff():
    # Even if XFF is present and direct IP is loopback, empty trusted list
    # must not trust the header.
    assert _run_client_ip("", "127.0.0.1", "9.9.9.9") == "127.0.0.1"


def test_exact_ip_match_honours_xff():
    assert _run_client_ip("127.0.0.1", "127.0.0.1", "9.9.9.9") == "9.9.9.9"


def test_exact_ip_mismatch_ignores_xff():
    assert _run_client_ip("127.0.0.1", "10.0.0.5", "9.9.9.9") == "10.0.0.5"


def test_cidr_match_honours_xff():
    # Docker compose auto-assigns from 172.16/12; simulate a dynamic bridge IP.
    assert _run_client_ip("172.16.0.0/12", "172.18.0.5", "9.9.9.9") == "9.9.9.9"


def test_cidr_boundary_in_range():
    assert _run_client_ip("172.16.0.0/12", "172.31.255.254", "9.9.9.9") == "9.9.9.9"


def test_cidr_boundary_out_of_range():
    # 172.32.x.x is outside 172.16.0.0/12.
    assert _run_client_ip("172.16.0.0/12", "172.32.0.1", "9.9.9.9") == "172.32.0.1"


def test_mixed_list_first_entry_matches():
    assert (
        _run_client_ip("127.0.0.1,172.16.0.0/12", "127.0.0.1", "9.9.9.9")
        == "9.9.9.9"
    )


def test_mixed_list_cidr_entry_matches():
    assert (
        _run_client_ip("127.0.0.1,172.16.0.0/12", "172.20.5.5", "9.9.9.9")
        == "9.9.9.9"
    )


def test_mixed_list_no_entry_matches():
    assert (
        _run_client_ip("127.0.0.1,172.16.0.0/12", "10.0.0.5", "9.9.9.9")
        == "10.0.0.5"
    )


def test_ipv6_exact_match_honours_xff():
    assert _run_client_ip("::1", "::1", "9.9.9.9") == "9.9.9.9"


def test_ipv6_cidr_match_honours_xff():
    assert _run_client_ip("fd00::/8", "fd12:3456::1", "9.9.9.9") == "9.9.9.9"


def test_xff_spoofed_leftmost_entry_is_ignored():
    """Proxies APPEND the peer they saw, so the leftmost entries are
    client-supplied. A client sending its own XFF must not be able to
    choose its rate-limit identity."""
    assert (
        _run_client_ip("127.0.0.1", "127.0.0.1", "1.2.3.4, 9.9.9.9")
        == "9.9.9.9"
    )


def test_xff_multi_hop_walks_past_trusted_proxies():
    """With a chain of trusted proxies, the rightmost entry that is NOT a
    trusted proxy is the first unforgeable hop."""
    assert (
        _run_client_ip(
            "127.0.0.1,10.0.0.0/8",
            "127.0.0.1",
            "9.9.9.9, 10.0.0.1, 10.0.0.2",
        )
        == "9.9.9.9"
    )


def test_xff_all_trusted_chain_falls_back_to_direct():
    # Proxy-to-proxy traffic with no external client in the chain.
    assert (
        _run_client_ip(
            "127.0.0.1,10.0.0.0/8", "127.0.0.1", "10.0.0.1, 10.0.0.2"
        )
        == "127.0.0.1"
    )


def test_xff_garbage_rightmost_falls_back_to_direct():
    # A malformed entry means everything left of it is untrustworthy.
    assert (
        _run_client_ip("127.0.0.1", "127.0.0.1", "9.9.9.9, not-an-ip")
        == "127.0.0.1"
    )


def test_xff_garbage_left_of_result_is_irrelevant():
    # The walk stops at the rightmost untrusted entry before reaching the
    # garbage the client planted further left.
    assert (
        _run_client_ip("127.0.0.1", "127.0.0.1", "garbage, 9.9.9.9")
        == "9.9.9.9"
    )


def test_unparseable_direct_ip_returns_unchanged():
    # request.client is None → direct_ip = "unknown". Must not crash; returns
    # the sentinel and ignores XFF.
    patched = Settings(trusted_proxies="127.0.0.1")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"9.9.9.9")],
        "client": None,
    }
    with patch.object(request_module, "settings", patched):
        assert request_module.client_ip(Request(scope)) == "unknown"
