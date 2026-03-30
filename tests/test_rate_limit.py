"""Rate limiting integration tests.

These tests require the server to be running with RATE_LIMIT_ENABLED=true.
They are marked with @pytest.mark.rate_limit and only run in the dedicated
rate limit test configuration (see run_tests.sh).
"""

import os
import uuid

import httpx
import pytest
import redis

pytestmark = pytest.mark.rate_limit

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")
# Test compose exposes Redis on 6380
REDIS_URL = os.environ.get("SHEAF_TEST_REDIS_URL", "redis://localhost:6380/0")


@pytest.fixture(autouse=True)
def flush_rate_limit_keys():
    """Clear all rate limit keys before each test so counters start fresh."""
    r = redis.from_url(REDIS_URL)
    for key in r.scan_iter("sheaf:rl:*"):
        r.delete(key)
    r.close()
    yield


@pytest.fixture
def anon_client() -> httpx.Client:
    with httpx.Client(base_url=BASE_URL) as c:
        yield c


def test_register_rate_limit(anon_client):
    """Registration is limited to 5/min per IP."""
    results = []
    for _ in range(8):
        resp = anon_client.post(
            "/v1/auth/register",
            json={
                "email": f"rl-reg-{uuid.uuid4().hex[:8]}@sheaf.dev",
                "password": "testpassword123",
            },
        )
        results.append(resp.status_code)

    assert 429 in results, f"Expected at least one 429, got: {results}"
    # First few should succeed
    assert results[0] == 201


def test_login_rate_limit(anon_client):
    """Login is limited to 10/min per IP."""
    # Register one user to have valid credentials
    email = f"rl-login-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = anon_client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201

    results = []
    for _ in range(15):
        resp = anon_client.post(
            "/v1/auth/login",
            json={"email": email, "password": "testpassword123"},
        )
        results.append(resp.status_code)

    assert 429 in results, f"Expected at least one 429, got: {results}"
    assert results[0] == 200


def test_rate_limit_returns_correct_headers(anon_client):
    """429 responses include rate limit headers."""
    for _ in range(8):
        resp = anon_client.post(
            "/v1/auth/register",
            json={
                "email": f"rl-hdr-{uuid.uuid4().hex[:8]}@sheaf.dev",
                "password": "testpassword123",
            },
        )
        if resp.status_code == 429:
            assert "x-ratelimit-limit" in resp.headers
            assert "x-ratelimit-remaining" in resp.headers
            assert "x-ratelimit-reset" in resp.headers
            assert "retry-after" in resp.headers
            # Remaining should be a non-negative integer
            assert int(resp.headers["x-ratelimit-remaining"]) >= 0
            return

    pytest.fail("Never hit 429 — rate limit may not be enabled")


def test_rate_limit_response_body(anon_client):
    """429 responses have a JSON body with detail."""
    for _ in range(8):
        resp = anon_client.post(
            "/v1/auth/register",
            json={
                "email": f"rl-body-{uuid.uuid4().hex[:8]}@sheaf.dev",
                "password": "testpassword123",
            },
        )
        if resp.status_code == 429:
            body = resp.json()
            assert "detail" in body
            assert "rate limit" in body["detail"].lower()
            return

    pytest.fail("Never hit 429 — rate limit may not be enabled")


def test_global_backstop_headers_on_success(anon_client):
    """Successful responses include global rate limit headers from middleware."""
    resp = anon_client.get("/health")
    assert resp.status_code == 200
    assert "x-ratelimit-limit" in resp.headers
    assert "x-ratelimit-remaining" in resp.headers


def test_different_endpoints_have_separate_limits(anon_client):
    """Rate limits are per-endpoint, not shared."""
    # Register a user for login (do this first, before burning register quota)
    email = f"rl-sep-login-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = anon_client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201

    # Hit register 4 more times (total 5 = at limit)
    for _ in range(4):
        anon_client.post(
            "/v1/auth/register",
            json={
                "email": f"rl-sep-{uuid.uuid4().hex[:8]}@sheaf.dev",
                "password": "testpassword123",
            },
        )

    # Login should still work (different endpoint, separate counter)
    resp = anon_client.post(
        "/v1/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 200
