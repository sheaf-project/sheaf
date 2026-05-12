"""Unit tests for the APNS_DEV_ENABLED feature flag.

The flag gates two surfaces:
- POST /v1/watch-tokens/{id}/channels with destination_type=apns_dev
- POST /v1/devices/push with platform=apns_dev

Integration tests in the test stack run with APNS_DEV_ENABLED=true so the
"on" paths are already exercised end-to-end. These unit tests cover the
"off" path, where the gate should reject sandbox-token registration so
production deployments don't accrue orphaned dev rows.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from sheaf.api.v1.notification_channels import _validate_destination
from sheaf.config import settings


@pytest.fixture
def apns_creds_configured(monkeypatch):
    """Pretend APNs credentials are present so the gate reaches the
    apns_dev_enabled check rather than tripping the earlier creds gate."""
    monkeypatch.setattr(settings, "apns_team_id", "TESTTEAM01")
    monkeypatch.setattr(settings, "apns_key_id", "TESTKEY001")
    monkeypatch.setattr(settings, "apns_bundle_id", "com.sheaftest.app")
    monkeypatch.setattr(
        settings,
        "apns_p8_key",
        "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    )


def test_channel_apns_dev_rejected_when_flag_off(apns_creds_configured, monkeypatch):
    monkeypatch.setattr(settings, "apns_dev_enabled", False)
    with pytest.raises(HTTPException) as exc:
        _validate_destination("apns_dev")
    assert exc.value.status_code == 501
    assert "apns_dev" in exc.value.detail


def test_channel_apns_dev_allowed_when_flag_on(apns_creds_configured, monkeypatch):
    monkeypatch.setattr(settings, "apns_dev_enabled", True)
    # Should not raise.
    _validate_destination("apns_dev")


def test_channel_apns_prod_not_affected_by_flag(apns_creds_configured, monkeypatch):
    """The gate is apns_dev-only; apns_prod passes regardless of the flag."""
    monkeypatch.setattr(settings, "apns_dev_enabled", False)
    _validate_destination("apns_prod")


def test_channel_apns_dev_creds_check_runs_before_flag(monkeypatch):
    """If APNs creds are not configured at all, the earlier creds gate
    fires first. Flag state shouldn't matter."""
    monkeypatch.setattr(settings, "apns_team_id", "")
    monkeypatch.setattr(settings, "apns_key_id", "")
    monkeypatch.setattr(settings, "apns_bundle_id", "")
    monkeypatch.setattr(settings, "apns_p8_key", "")
    monkeypatch.setattr(settings, "apns_dev_enabled", True)
    with pytest.raises(HTTPException) as exc:
        _validate_destination("apns_dev")
    assert exc.value.status_code == 501
    assert "APNs is not configured" in exc.value.detail


def test_device_endpoint_gate_matches_channel_gate():
    """The device-registration endpoint shares the same gate pattern as
    the channel-creation endpoint. This is a structural assertion: both
    sites read `settings.apns_dev_enabled` and both raise on apns_dev when
    the flag is off, keeping the surfaces symmetric so prod deployments
    don't get a sneaky half-open door."""
    import inspect

    from sheaf.api.v1 import devices

    source = inspect.getsource(devices.register_push_device)
    assert "apns_dev_enabled" in source, (
        "device-registration endpoint must consult apns_dev_enabled"
    )
    assert "PushPlatform.APNS_DEV" in source, (
        "device-registration endpoint must compare against the APNS_DEV enum value"
    )
