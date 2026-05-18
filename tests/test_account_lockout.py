"""Failed-attempt lockout coverage for the credentialed endpoints.

After the lockout refactor, `failed_login_count` / `locked_until` is a
single shared state: TOTP disable, recovery-code regeneration, and the
account-data endpoint all consult and increment it, so a stolen session
can't brute-force a short code by spreading attempts across endpoints.
"""

import time
import uuid

import httpx
import pyotp

# Comfortably above the default login_max_failures (10) so the lockout
# trips regardless of small config drift.
_ATTEMPTS = 15


def _register(client: httpx.Client, password: str = "testpassword123") -> str:
    email = f"lockout-{uuid.uuid4().hex[:10]}@sheaf.dev"
    r = client.post(
        "/v1/auth/register", json={"email": email, "password": password}
    )
    assert r.status_code == 201, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return email


def _enrol_totp(client: httpx.Client) -> pyotp.TOTP:
    setup = client.post("/v1/auth/totp/setup")
    assert setup.status_code == 200, setup.text
    totp = pyotp.TOTP(setup.json()["secret"])
    verify = client.post("/v1/auth/totp/verify", json={"code": totp.now()})
    assert verify.status_code == 204, verify.text
    return totp


def _stale_code(totp: pyotp.TOTP) -> str:
    """A real 6-digit code for an hour-old window — well outside the
    accepted skew, so it's a guaranteed-wrong (non-flaky) code."""
    return totp.at(int(time.time()) - 3600)


def test_account_data_locks_after_repeated_bad_passwords(client: httpx.Client):
    _register(client)
    statuses = [
        client.post("/v1/account/data", json={"password": "wrong-password"}).status_code
        for _ in range(_ATTEMPTS)
    ]
    # The wrong password is a step-up denial (403) until the lockout trips,
    # after which the endpoint short-circuits to 423.
    assert 423 in statuses, statuses
    assert statuses[0] == 403, statuses
    assert statuses[-1] == 423, statuses


def test_lockout_is_shared_across_endpoints(client: httpx.Client):
    """Failures on /account/data must lock /login too — the whole point of
    the unified lockout state."""
    password = "testpassword123"
    email = _register(client, password)

    for _ in range(_ATTEMPTS):
        client.post("/v1/account/data", json={"password": "wrong-password"})

    # Even the *correct* password is now refused at login.
    resp = client.post(
        "/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 423, resp.text


def test_totp_disable_locks_after_repeated_bad_codes(client: httpx.Client):
    password = "testpassword123"
    email = _register(client, password)
    totp = _enrol_totp(client)
    wrong = _stale_code(totp)

    statuses = [
        client.post(
            "/v1/auth/totp/disable",
            json={"email": email, "password": password, "totp_code": wrong},
        ).status_code
        for _ in range(_ATTEMPTS)
    ]
    assert 423 in statuses, statuses
    assert statuses[0] == 400, statuses
    assert statuses[-1] == 423, statuses


def test_regenerate_recovery_codes_locks_after_bad_codes(client: httpx.Client):
    _register(client)
    totp = _enrol_totp(client)
    wrong = _stale_code(totp)

    statuses = [
        client.post(
            "/v1/auth/totp/regenerate-recovery-codes", json={"code": wrong}
        ).status_code
        for _ in range(_ATTEMPTS)
    ]
    assert 423 in statuses, statuses
    assert statuses[0] == 400, statuses
    assert statuses[-1] == 423, statuses
