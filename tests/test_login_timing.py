"""Login timing-oracle guard.

Unknown-email logins used to skip Argon2 entirely (the `or` short-
circuits before verify_password), returning ~100ms faster than a
wrong-password attempt on a real account and letting account existence
be probed by latency. The fix spends an equivalent Argon2 verify on the
unknown-user branch. These tests guard that the equaliser keeps doing
real work and that the response itself leaks nothing.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


def test_dummy_verify_actually_runs_argon2():
    """The equaliser must do a real Argon2 verify, not a no-op - otherwise
    the oracle reopens. A no-op string compare is microseconds; Argon2 is
    tens of milliseconds, so a generous floor distinguishes them without
    being flaky."""
    from sheaf.auth.passwords import dummy_verify

    async def _timed() -> float:
        start = time.perf_counter()
        await dummy_verify()
        return time.perf_counter() - start

    elapsed = asyncio.run(_timed())
    assert elapsed > 0.005, (
        f"dummy_verify returned in {elapsed * 1000:.2f}ms - too fast to "
        "have run Argon2; the timing equaliser has degraded to a no-op"
    )


def test_unknown_email_and_wrong_password_are_indistinguishable(
    client: httpx.Client,
):
    """The response body and status for an unknown email must match a
    wrong password on a real account - no existence oracle in the reply."""
    email = f"timing-{uuid.uuid4().hex[:8]}@sheaf.dev"
    reg = client.post(
        "/v1/auth/register", json={"email": email, "password": "testpassword123"}
    )
    assert reg.status_code == 201, reg.text

    wrong_pw = client.post(
        "/v1/auth/login", json={"email": email, "password": "wrong-password"}
    )
    unknown = client.post(
        "/v1/auth/login",
        json={
            "email": f"nobody-{uuid.uuid4().hex[:8]}@sheaf.dev",
            "password": "wrong-password",
        },
    )

    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()
