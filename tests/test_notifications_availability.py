"""Which transports an instance admits to having, and what it says instead.

Host-only: `mobile_push_available` is a pure function of settings, and the
reason is a constant. Neither needs a stack, and the stack could not test the
interesting case anyway - it injects dummy FCM and APNs credentials at
startup, so every run there is the configured branch.

The point of the module under test is that one predicate answers the question
for both the create endpoint and the picker. A second copy of the logic is
exactly the bug this removes: a picker that offers a channel type the create
call then refuses.
"""

from __future__ import annotations

import pytest

from sheaf.config import Settings
from sheaf.services.notifications.availability import (
    MOBILE_PUSH_UNAVAILABLE_REASON,
    apns_configured,
    fcm_configured,
    mobile_push_available,
)

# The minimum a Settings needs to construct; everything else defaults, which
# is the state of a self-hosted instance that has not set up mobile push.
_BASE = {
    "sheaf_encryption_key": "0" * 64,
    "jwt_secret_key": "test-jwt-secret-not-for-production",
}


def _settings(**overrides) -> Settings:
    return Settings(**{**_BASE, **overrides})


def test_a_default_instance_has_no_mobile_push():
    """The case that matters: someone stood up Sheaf and set nothing.

    This is essentially every self-hosted instance, which is why the picker
    has to handle it as a normal state rather than as an error.
    """
    assert mobile_push_available(_settings()) is False


@pytest.mark.parametrize(
    "creds",
    [
        {"fcm_service_account_path": "/etc/sheaf/fcm.json"},
        {"fcm_service_account_json": '{"project_id": "x"}'},
    ],
)
def test_either_form_of_fcm_credential_counts(creds: dict):
    """Path or inline JSON. Both are how an operator supplies the same thing."""
    s = _settings(**creds)
    assert fcm_configured(s) is True
    assert mobile_push_available(s) is True


def test_apns_needs_every_part():
    """A .p8 on its own cannot be used.

    Dropping any one of these leaves a credential that cannot say who it is
    or what app it is addressing, so treating a partial set as "configured"
    would accept channels that fail on every single delivery.
    """
    full = {
        "apns_team_id": "TEAM123456",
        "apns_key_id": "KEY1234567",
        "apns_bundle_id": "sh.sheaf.app",
        "apns_p8_key": "-----BEGIN PRIVATE KEY-----",
    }
    assert apns_configured(_settings(**full)) is True
    for missing in full:
        partial = {k: v for k, v in full.items() if k != missing}
        assert apns_configured(_settings(**partial)) is False, missing


def test_one_platform_is_enough():
    """A single-platform deployment still accepts mobile_push channels.

    Refusing the type outright because the OTHER platform is unconfigured
    would deny Android users an Android-only instance can serve perfectly
    well. Delivery to the missing platform is a no-op, not a refusal.
    """
    android_only = _settings(fcm_service_account_path="/etc/sheaf/fcm.json")
    assert apns_configured(android_only) is False
    assert mobile_push_available(android_only) is True


def test_the_reason_says_why_rather_than_just_what():
    """"Not configured" invites a self-hoster to go looking for the setting.

    There isn't one: the blocker is that a push credential is bound to an app
    build. The text has to carry that, and has to leave them with something
    that does work, or it is just a politer dead end.
    """
    reason = MOBILE_PUSH_UNAVAILABLE_REASON
    assert "app build" in reason
    assert "ntfy" in reason and "Pushover" in reason
    # It renders in a browser, in a terminal and in whatever a third-party
    # client uses, so it stays plain ASCII: no smart quotes, no fancy dashes.
    assert reason.isascii()
