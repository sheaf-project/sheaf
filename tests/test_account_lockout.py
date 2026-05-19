"""Failed-attempt lockout coverage for the credentialed endpoints.

After the lockout refactor, `failed_login_count` / `locked_until` is a
single shared state: TOTP disable, recovery-code regeneration, and the
account-data endpoint all consult and increment it, so a stolen session
can't brute-force a short code by spreading attempts across endpoints.
"""

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pyotp


def _login(client: httpx.Client, email: str, password: str) -> int:
    return client.post(
        "/v1/auth/login", json={"email": email, "password": password}
    ).status_code


async def _db_lockout_op(email: str, *, read: bool, count: int = 0):
    """Read or set a user's failed-attempt state directly in the DB.

    Used to exercise the lockout reset path without waiting out a real
    15-minute lockout window.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from sheaf.config import settings
    from sheaf.crypto import blind_index
    from sheaf.models.user import User

    db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
    engine = create_async_engine(db_url)
    session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session() as db:
            result = await db.execute(
                select(User).where(User.email_hash == blind_index(email))
            )
            user = result.scalar_one()
            if read:
                return user.failed_login_count
            # Simulate an expired lockout: counter at the cap, window past.
            user.failed_login_count = count
            user.locked_until = datetime.now(UTC) - timedelta(minutes=1)
            await db.commit()
            return None
    finally:
        await engine.dispose()

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


def test_login_locks_after_repeated_failures(client: httpx.Client):
    email = _register(client)
    statuses = [
        _login(client, email, "wrong-password") for _ in range(_ATTEMPTS)
    ]
    assert 423 in statuses, statuses
    assert statuses[0] == 401, statuses


def test_correct_password_rejected_during_lockout(client: httpx.Client):
    password = "testpassword123"
    email = _register(client, password)
    for _ in range(_ATTEMPTS):
        _login(client, email, "wrong-password")
    # The lockout window rejects even the genuine password.
    assert _login(client, email, password) == 423


def test_successful_login_clears_failure_counter(client: httpx.Client):
    """Five failures, a success, then five more must not lock — the success
    reset the counter, so the second batch starts from zero rather than
    accumulating past the threshold."""
    password = "testpassword123"
    email = _register(client, password)
    for _ in range(5):
        assert _login(client, email, "wrong-password") == 401
    assert _login(client, email, password) == 200  # clears the counter
    for _ in range(5):
        assert _login(client, email, "wrong-password") == 401
    # Still unlocked — the genuine password works.
    assert _login(client, email, password) == 200


def test_expired_lockout_resets_counter_to_one(client: httpx.Client):
    """A stale (expired) lockout doesn't carry its old count forward: the
    next failure starts a fresh count of 1, not threshold+1."""
    email = _register(client)
    asyncio.run(_db_lockout_op(email, read=False, count=10))
    # The expired window lets the attempt through to a normal 401...
    assert _login(client, email, "wrong-password") == 401
    # ...and the counter restarted at 1 rather than incrementing to 11.
    assert asyncio.run(_db_lockout_op(email, read=True)) == 1


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
