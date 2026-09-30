"""The passkey availability rule, as a pure function of configuration.

No server stack: ``resolve_relying_party`` takes the two settings it depends
on and answers. What is pinned here is the contract the rest of the feature
builds on:

* the RP ID is the host of the base URL, an override may only name a
  registrable parent of that host, and the origin is rebuilt from the parsed
  URL rather than echoed;
* plain HTTP is refused except on the loopback names browsers treat as
  secure, which is what keeps the feature alive in the test stack and on a
  local dev server;
* every refusal carries a stable reason, because "not available here" and
  "broken" must never look the same to a client.
"""

from __future__ import annotations

import pytest

from sheaf.auth.passkeys import (
    REASON_INSECURE_BASE_URL,
    REASON_MALFORMED_BASE_URL,
    REASON_NO_BASE_URL,
    REASON_RP_ID_INVALID,
    REASON_RP_ID_NOT_PARENT_OF_HOST,
    resolve_relying_party,
)


def _rp(base_url: str, override: str = ""):
    resolution = resolve_relying_party(base_url, override)
    assert resolution.available, resolution.reason
    assert resolution.reason is None
    return resolution.rp


def _reason(base_url: str, override: str = "") -> str:
    resolution = resolve_relying_party(base_url, override)
    assert not resolution.available
    assert resolution.rp is None
    assert resolution.reason is not None
    return resolution.reason


# --- the ordinary instance ---------------------------------------------------


def test_https_host_is_the_rp_id_and_the_origin():
    rp = _rp("https://sheaf.example.net")
    assert rp.rp_id == "sheaf.example.net"
    assert rp.origin == "https://sheaf.example.net"


def test_explicit_port_stays_in_the_origin_but_not_the_rp_id():
    rp = _rp("https://sheaf.example.net:8443")
    assert rp.rp_id == "sheaf.example.net"
    assert rp.origin == "https://sheaf.example.net:8443"


def test_origin_is_rebuilt_not_echoed():
    # Mixed case, a path and a trailing slash in the configured value must
    # not reach the origin a ceremony's client data is compared against.
    rp = _rp("HTTPS://Sheaf.Example.NET/some/base/")
    assert rp.rp_id == "sheaf.example.net"
    assert rp.origin == "https://sheaf.example.net"


def test_surrounding_whitespace_is_ignored():
    assert _rp("  https://sheaf.example.net  ").rp_id == "sheaf.example.net"


# --- the secure-context rule -------------------------------------------------


def test_unset_base_url_is_unavailable():
    assert _reason("") == REASON_NO_BASE_URL
    assert _reason("   ") == REASON_NO_BASE_URL


def test_plain_http_on_a_real_host_is_unavailable():
    assert _reason("http://sheaf.example.net") == REASON_INSECURE_BASE_URL


@pytest.mark.parametrize(
    "base_url, expected_rp_id, expected_origin",
    [
        ("http://localhost:8001", "localhost", "http://localhost:8001"),
        ("http://127.0.0.1:8000", "127.0.0.1", "http://127.0.0.1:8000"),
        ("http://[::1]:8000", "::1", "http://[::1]:8000"),
        ("http://localhost", "localhost", "http://localhost"),
    ],
)
def test_loopback_names_are_exempt_from_https(base_url, expected_rp_id, expected_origin):
    # The test stack's own base URL is the first of these. Without this
    # exemption the feature would report unavailable there and the suite
    # would test nothing.
    rp = _rp(base_url)
    assert rp.rp_id == expected_rp_id
    assert rp.origin == expected_origin


def test_lookalike_loopback_names_are_not_exempt():
    # Neither a subdomain of localhost nor a private-range address is a
    # secure context everywhere, so neither gets the exemption.
    assert _reason("http://app.localhost") == REASON_INSECURE_BASE_URL
    assert _reason("http://192.168.1.10") == REASON_INSECURE_BASE_URL


@pytest.mark.parametrize(
    "base_url",
    [
        "sheaf.example.net",  # no scheme
        "ftp://sheaf.example.net",  # wrong scheme
        "https://",  # no host
        "https://sheaf.example.net:notaport",  # unparseable port
        "not a url at all",
    ],
)
def test_malformed_base_url_is_unavailable_not_an_error(base_url):
    assert _reason(base_url) == REASON_MALFORMED_BASE_URL


# --- the override ------------------------------------------------------------


def test_override_equal_to_the_host_is_accepted():
    assert _rp("https://sheaf.example.net", "sheaf.example.net").rp_id == "sheaf.example.net"


def test_override_may_name_a_registrable_parent():
    rp = _rp("https://sheaf.example.net", "example.net")
    assert rp.rp_id == "example.net"
    # The origin is still the instance's own, not the parent's.
    assert rp.origin == "https://sheaf.example.net"


def test_override_that_is_not_a_parent_is_refused():
    assert _reason("https://sheaf.example.net", "other.net") == REASON_RP_ID_NOT_PARENT_OF_HOST


def test_override_must_be_a_label_boundary_parent():
    # "ample.net" is a string suffix of the host but not a parent domain.
    assert _reason("https://sheaf.example.net", "ample.net") == REASON_RP_ID_NOT_PARENT_OF_HOST


def test_override_cannot_be_a_child_of_the_host():
    assert _reason("https://example.net", "sheaf.example.net") == REASON_RP_ID_NOT_PARENT_OF_HOST


@pytest.mark.parametrize(
    "override",
    [
        "https://example.net",  # a URL, not a host
        "example.net:443",  # a port
        "example.net/",  # a path
        "Example.NET",  # not lowercase
        "-example.net",  # bad label
        "exa mple.net",
    ],
)
def test_override_with_the_wrong_shape_is_refused(override):
    assert _reason("https://sheaf.example.net", override) == REASON_RP_ID_INVALID


def test_override_on_an_ip_literal_only_matches_itself():
    assert _rp("http://127.0.0.1:8000", "127.0.0.1").rp_id == "127.0.0.1"
    assert _reason("http://127.0.0.1:8000", "0.0.1") == REASON_RP_ID_NOT_PARENT_OF_HOST


def test_the_scheme_rule_wins_over_the_override():
    # An override does not rescue a plain-HTTP instance.
    assert _reason("http://sheaf.example.net", "example.net") == REASON_INSECURE_BASE_URL
