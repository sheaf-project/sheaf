"""Opt-in private-CIDR allowlist for outbound webhook / ntfy delivery.

Two layers are covered here, both pure-Python (no DB, no network):

- The SSRF guard (`_is_disallowed` / `allowlist_override_applies`) honours an
  operator allowlist while keeping a hard-never carve-out for cloud metadata,
  the unspecified address, and multicast.
- The config plumbing: the allowlist is parsed to networks, dropped entirely
  in SaaS mode, and a malformed entry fails validation at construction.

The guard tests drive the real `settings` object via monkeypatch so the
SaaS-mode carve-out on the property is exercised end to end.
"""

from __future__ import annotations

import ipaddress

import pytest

from sheaf.config import Settings, SheafMode, settings
from sheaf.services.notifications.safe_http import (
    _is_disallowed,
    allowlist_override_applies,
)


def _addr(s: str):
    return ipaddress.ip_address(s)


@pytest.fixture
def allowlist(monkeypatch):
    """Set the allowlist CIDRs on the live settings object (selfhosted mode)
    and return a setter so each test picks its own ranges. The property
    recomputes from these, so the SaaS carve-out is exercised too."""
    monkeypatch.setattr(settings, "sheaf_mode", SheafMode.SELFHOSTED)

    def _set(cidrs: str):
        monkeypatch.setattr(settings, "webhook_allowed_private_cidrs", cidrs)

    _set("")
    return _set


# ---------------------------------------------------------------------------
# Guard: allowlist honoured, with the hard-never carve-out


def test_allowed_private_is_permitted(allowlist):
    allowlist("192.168.1.0/24")
    assert _is_disallowed(_addr("192.168.1.50")) is False


def test_same_private_blocked_when_not_listed(allowlist):
    # Nothing allowlisted: the exact address the previous test permitted is
    # blocked again.
    allowlist("")
    assert _is_disallowed(_addr("192.168.1.50")) is True


def test_non_listed_private_stays_blocked(allowlist):
    # A different private range than the one allowlisted is still blocked.
    allowlist("192.168.1.0/24")
    assert _is_disallowed(_addr("10.0.0.1")) is True


def test_public_address_always_permitted(allowlist):
    allowlist("")
    assert _is_disallowed(_addr("93.184.216.34")) is False
    allowlist("192.168.1.0/24")
    assert _is_disallowed(_addr("93.184.216.34")) is False


def test_ipv4_metadata_blocked_even_when_allowlisted(allowlist):
    # An allowlist that covers the IMDS address must not unblock it.
    allowlist("169.254.0.0/16")
    assert _is_disallowed(_addr("169.254.169.254")) is True


def test_ipv6_metadata_blocked_even_when_allowlisted(allowlist):
    # fc00::/7 (ULA) covers fd00:ec2::254; the hard-never carve-out wins.
    allowlist("fc00::/7")
    assert _is_disallowed(_addr("fd00:ec2::254")) is True


def test_unspecified_and_multicast_blocked_even_when_allowlisted(allowlist):
    allowlist("0.0.0.0/0")
    assert _is_disallowed(_addr("0.0.0.0")) is True
    assert _is_disallowed(_addr("224.0.0.1")) is True


# ---------------------------------------------------------------------------
# The "opt-in was exercised" signal used for the metric


def test_override_applies_only_for_allowlisted_private(allowlist):
    allowlist("192.168.1.0/24")
    # Blocked-by-default private that the allowlist rescued: counts.
    assert allowlist_override_applies(_addr("192.168.1.50")) is True
    # Public address: allowed regardless, so the opt-in wasn't the reason.
    assert allowlist_override_applies(_addr("93.184.216.34")) is False
    # Private but not listed: not deliverable at all, so not an override.
    assert allowlist_override_applies(_addr("10.0.0.1")) is False


def test_override_never_applies_to_metadata(allowlist):
    allowlist("169.254.0.0/16")
    assert allowlist_override_applies(_addr("169.254.169.254")) is False


def test_override_false_with_empty_allowlist(allowlist):
    allowlist("")
    assert allowlist_override_applies(_addr("192.168.1.50")) is False


# ---------------------------------------------------------------------------
# Config: parsing, the SaaS carve-out, and fail-fast validation


def test_selfhosted_honours_allowlist(monkeypatch):
    monkeypatch.delenv("WEBHOOK_ALLOWED_PRIVATE_CIDRS", raising=False)
    s = Settings(
        _env_file=None,
        sheaf_mode=SheafMode.SELFHOSTED,
        webhook_allowed_private_cidrs="192.168.1.0/24, 10.10.0.0/16",
    )
    nets = s.webhook_allowed_private_networks
    assert ipaddress.ip_network("192.168.1.0/24") in nets
    assert ipaddress.ip_network("10.10.0.0/16") in nets


def test_saas_mode_drops_allowlist(monkeypatch):
    monkeypatch.delenv("WEBHOOK_ALLOWED_PRIVATE_CIDRS", raising=False)
    s = Settings(
        _env_file=None,
        sheaf_mode=SheafMode.SAAS,
        webhook_allowed_private_cidrs="192.168.1.0/24",
    )
    # The raw string is retained, but the property refuses to honour it in
    # SaaS mode: the hosted instance must never reach a private range.
    assert s.webhook_allowed_private_cidrs == "192.168.1.0/24"
    assert s.webhook_allowed_private_networks == []


def test_empty_allowlist_is_empty_list(monkeypatch):
    monkeypatch.delenv("WEBHOOK_ALLOWED_PRIVATE_CIDRS", raising=False)
    s = Settings(_env_file=None)
    assert s.webhook_allowed_private_networks == []


def test_bad_cidr_fails_validation(monkeypatch):
    monkeypatch.delenv("WEBHOOK_ALLOWED_PRIVATE_CIDRS", raising=False)
    with pytest.raises(ValueError, match="not-a-cidr"):
        Settings(_env_file=None, webhook_allowed_private_cidrs="192.168.1.0/24,not-a-cidr")
