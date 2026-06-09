"""CSRF Origin-check middleware behaviour.

Cookie-authenticated unsafe requests with a browser Origin header must
come from an allowed origin. Bearer-only requests, safe methods, and
requests without an Origin header (non-browser clients) pass untouched.
"""

import os
import uuid

import httpx

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


def _cookie_login(client: httpx.Client) -> str:
    """Register; the client keeps the session/refresh cookies. Returns token."""
    email = f"csrf-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201, resp.text
    assert client.cookies.get("sheaf_session"), "expected session cookie"
    return resp.json()["access_token"]


def test_cookie_mutation_with_foreign_origin_rejected(client: httpx.Client):
    token = _cookie_login(client)
    resp = client.post(
        "/v1/members",
        json={"name": "CsrfTest"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": "https://evil.example",
        },
    )
    assert resp.status_code == 403, resp.text
    assert "Cross-origin" in resp.json()["detail"]


def test_cookie_mutation_with_null_origin_rejected(client: httpx.Client):
    token = _cookie_login(client)
    resp = client.post(
        "/v1/members",
        json={"name": "CsrfNull"},
        headers={"Authorization": f"Bearer {token}", "Origin": "null"},
    )
    assert resp.status_code == 403, resp.text


def test_cookie_mutation_with_matching_origin_allowed(client: httpx.Client):
    token = _cookie_login(client)
    host = httpx.URL(BASE_URL).netloc.decode()
    resp = client.post(
        "/v1/members",
        json={"name": "CsrfSameOrigin"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": f"http://{host}",
        },
    )
    assert resp.status_code == 201, resp.text


def test_cookie_mutation_without_origin_allowed(client: httpx.Client):
    # Non-browser clients don't send Origin and can't be CSRF'd into
    # attaching cookies; they must keep working.
    token = _cookie_login(client)
    resp = client.post(
        "/v1/members",
        json={"name": "CsrfNoOrigin"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text


def test_bearer_only_request_ignores_origin(client: httpx.Client):
    # No ambient credential = no CSRF surface; foreign Origin is fine.
    token = _cookie_login(client)
    bare = httpx.Client(base_url=BASE_URL)
    resp = bare.post(
        "/v1/members",
        json={"name": "CsrfBearer"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": "https://evil.example",
        },
    )
    assert resp.status_code == 201, resp.text


def test_safe_method_with_foreign_origin_allowed(client: httpx.Client):
    token = _cookie_login(client)
    resp = client.get(
        "/v1/members",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": "https://evil.example",
        },
    )
    assert resp.status_code == 200, resp.text
