import asyncio
import os
import uuid
from collections.abc import Generator

import httpx
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


@pytest.fixture
def client() -> Generator[httpx.Client]:
    with httpx.Client(base_url=BASE_URL) as c:
        yield c


@pytest.fixture
def auth_client() -> Generator[httpx.Client]:
    """Authenticated client — registers a unique user per test."""
    with httpx.Client(base_url=BASE_URL) as c:
        email = f"test-{uuid.uuid4().hex[:8]}@sheaf.dev"
        resp = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert resp.status_code == 201
        token = resp.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


@pytest.fixture
def admin_client() -> Generator[httpx.Client]:
    """Authenticated client for a freshly registered admin user.

    Promotes the user to is_admin=True directly via the DB so tests don't
    depend on SHEAF_ADMIN_EMAILS being configured on the running server.
    """
    from sqlalchemy import select
    from sheaf.crypto import blind_index
    from sheaf.database import async_session_factory
    from sheaf.models.user import User

    email = f"admin-{uuid.uuid4().hex[:8]}@sheaf.dev"

    with httpx.Client(base_url=BASE_URL) as c:
        resp = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert resp.status_code == 201
        token = resp.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"

        # Promote directly via DB.
        # Uses SHEAF_TEST_DB_URL if set (useful when running tests outside Docker
        # where the default DATABASE_URL host 'db' is not reachable).
        async def _promote() -> None:
            from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
            from sqlalchemy.orm import sessionmaker

            db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
            engine = create_async_engine(db_url)
            async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with async_session() as db:
                email_hash = blind_index(email)
                result = await db.execute(select(User).where(User.email_hash == email_hash))
                user = result.scalar_one()
                user.is_admin = True
                await db.commit()
            await engine.dispose()

        asyncio.run(_promote())
        yield c
