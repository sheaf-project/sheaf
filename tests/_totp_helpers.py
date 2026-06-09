"""TOTP test helpers.

Server-side, every accepted TOTP code is single-use (replay guard in
sheaf.auth.totp.verify_code_once). Tests that drive several TOTP-gated
actions inside one 30-second timestep would otherwise need real waits
between steps; wiping the consumed-code markers lets them reuse the
current code deterministically. Production behaviour stays covered by
the dedicated replay tests in test_totp.py.
"""

from __future__ import annotations

import os

import redis


def clear_totp_replay() -> None:
    """Delete all consumed-TOTP markers from the test Redis."""
    url = os.environ.get("SHEAF_TEST_REDIS_URL")
    assert url, (
        "SHEAF_TEST_REDIS_URL must be set to clear TOTP replay markers "
        "(run via run_tests.sh)"
    )
    r = redis.Redis.from_url(url)
    try:
        for key in r.scan_iter("sheaf:totp_used:*"):
            r.delete(key)
    finally:
        r.close()
