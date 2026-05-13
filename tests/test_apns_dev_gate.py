"""Unit tests for the APNS_DEV_ENABLED feature flag.

After the mobile_push collapse, the flag only matters at one surface:

- POST /v1/devices/push with platform=apns_dev

The channel-creation gate is gone — there's no per-channel apns_dev
type anymore (the unified mobile_push type covers all platforms and
the dispatcher routes per-token via the device's `platform` column).
The device-registration gate still exists so a prod deployment can't
accrue orphaned sandbox device-token rows that APNs would bounce at
delivery time.
"""

from __future__ import annotations


def test_device_endpoint_still_gates_apns_dev():
    """The device-registration endpoint must consult `apns_dev_enabled`
    and compare against `PushPlatform.APNS_DEV` — structural assertion
    so a future refactor that drops the gate is caught here rather than
    by an operator discovering orphaned dev rows in prod."""
    import inspect

    from sheaf.api.v1 import devices

    source = inspect.getsource(devices.register_push_device)
    assert "apns_dev_enabled" in source, (
        "device-registration endpoint must consult apns_dev_enabled"
    )
    assert "PushPlatform.APNS_DEV" in source, (
        "device-registration endpoint must compare against the APNS_DEV enum value"
    )
