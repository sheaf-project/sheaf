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
