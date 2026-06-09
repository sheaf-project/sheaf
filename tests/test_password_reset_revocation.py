"""Completing a password reset revokes everything a compromiser holds.

The reset flow is the canonical "I've been compromised" recovery path,
so redeeming a reset token must kill all live sessions (and with them
the session-bound access/refresh tokens) and clear lockout state — the
same posture as change-password, minus the calling-session exemption
(the reset flow is unauthenticated, so there is no session to spare).

The reset token is planted directly in the DB (the test stack has no
mail backend); the token hash is computed with the same JWT secret the
server uses, exported by run_tests.sh.
"""

import asyncio
import os
import secrets
import uuid
from datetime import UTC, datetime

import httpx

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


def _plant_reset_token(email: str) -> str:
    """Write a valid reset-token hash onto the user row, return the raw token."""
    token = secrets.token_urlsafe(32)

    async def _run() -> None:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.crypto import blind_index, hash_mail_token
        from sheaf.models.user import User

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as db:
            result = await db.execute(
                select(User).where(User.email_hash == blind_index(email))
            )
            user = result.scalar_one()
            user.password_reset_token = hash_mail_token(token)
            user.password_reset_sent_at = datetime.now(UTC)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())
    return token


def test_reset_password_revokes_all_sessions(client: httpx.Client):
    email = f"reset-rev-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get("/v1/auth/me").status_code == 200

    raw = _plant_reset_token(email)
    reset = client.post(
        "/v1/auth/reset-password",
        json={"token": raw, "new_password": "newpassword456"},
    )
    assert reset.status_code == 200, reset.text

    # The pre-reset session-bound access token is dead.
    assert client.get("/v1/auth/me").status_code == 401

    # The new password works; the old one doesn't.
    del client.headers["Authorization"]
    assert (
        client.post(
            "/v1/auth/login",
            json={"email": email, "password": "testpassword123"},
        ).status_code
        == 401
    )
    login = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "newpassword456"},
    )
    assert login.status_code == 200, login.text
