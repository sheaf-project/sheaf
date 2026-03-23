import asyncio
import os
import uuid
from collections.abc import Generator

import httpx
import pyotp
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")

# Set by run_tests.sh to tell the suite which server config is active.
# Controls which config-specific tests are skipped.
_ADMIN_AUTH_LEVEL = os.environ.get("SHEAF_TEST_ADMIN_AUTH_LEVEL", "none")
_SHEAF_MODE = os.environ.get("SHEAF_TEST_MODE", "selfhosted")


def pytest_collection_modifyitems(items):
    for item in items:
        if "admin_auth_password" in item.keywords and _ADMIN_AUTH_LEVEL != "password":
            item.add_marker(pytest.mark.skip("requires SHEAF_TEST_ADMIN_AUTH_LEVEL=password"))
        if "admin_auth_totp" in item.keywords and _ADMIN_AUTH_LEVEL != "totp":
            item.add_marker(pytest.mark.skip("requires SHEAF_TEST_ADMIN_AUTH_LEVEL=totp"))
        if "saas" in item.keywords and _SHEAF_MODE != "saas":
            item.add_marker(pytest.mark.skip("requires SHEAF_TEST_MODE=saas"))


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


def _promote_to_admin(email: str) -> None:
    """Directly promote a user to admin via DB. Uses SHEAF_TEST_DB_URL if set."""
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker
        from sheaf.config import settings
        from sheaf.models.user import User

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

    asyncio.run(_run())


def _complete_step_up(c: httpx.Client) -> None:
    """Complete admin step-up auth based on what the server has configured.

    - none:     POST /admin/auth with no credential (always succeeds).
    - password: POST /admin/auth with the test password.
    - totp:     enrol TOTP on the account, then POST /admin/auth with a valid code.
    """
    status_resp = c.get("/v1/admin/auth")
    assert status_resp.status_code == 200
    level = status_resp.json()["level"]

    if level == "none":
        resp = c.post("/v1/admin/auth", json={})
        assert resp.status_code == 200

    elif level == "password":
        resp = c.post("/v1/admin/auth", json={"password": "testpassword123"})
        assert resp.status_code == 200, resp.text

    elif level == "totp":
        # Enrol TOTP on the account first
        setup_resp = c.post("/v1/auth/totp/setup")
        assert setup_resp.status_code == 200
        secret = setup_resp.json()["secret"]
        totp = pyotp.TOTP(secret)
        verify_resp = c.post("/v1/auth/totp/verify", json={"code": totp.now()})
        assert verify_resp.status_code == 204, verify_resp.text
        # Now complete step-up
        resp = c.post("/v1/admin/auth", json={"totp_code": totp.now()})
        assert resp.status_code == 200, resp.text


@pytest.fixture
def admin_client() -> Generator[httpx.Client]:
    """Authenticated admin client — promoted via DB, step-up completed automatically.

    Adapts to whatever ADMIN_AUTH_LEVEL the server is running with, so all
    admin tests pass regardless of server config.
    """
    email = f"admin-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=BASE_URL) as c:
        resp = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert resp.status_code == 201
        token = resp.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"

        _promote_to_admin(email)
        _complete_step_up(c)
        yield c


@pytest.fixture
def raw_admin_client() -> Generator[httpx.Client]:
    """Authenticated admin client with NO step-up completed.

    Use this to test that admin endpoints correctly enforce step-up.
    """
    email = f"admin-raw-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=BASE_URL) as c:
        resp = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert resp.status_code == 201
        token = resp.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"

        _promote_to_admin(email)
        yield c
