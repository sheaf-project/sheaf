"""Tests for API key creation, listing, revocation, and scope enforcement."""

import os

import httpx
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


def _create_key(client: httpx.Client, name: str = "test key", scopes: list[str] | None = None):
    resp = client.post(
        "/v1/auth/keys",
        json={"name": name, "scopes": scopes or ["members:read"]},
    )
    assert resp.status_code == 201
    return resp.json()


def _key_client(plaintext: str) -> httpx.Client:
    """Return a bare client authenticated only with the given API key."""
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {plaintext}"},
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_create_key_returns_plaintext_once(auth_client: httpx.Client):
    data = _create_key(auth_client)
    assert data["key"].startswith("sk_")
    assert data["name"] == "test key"
    assert "members:read" in data["scopes"]
    assert "id" in data


def test_list_keys(auth_client: httpx.Client):
    _create_key(auth_client, "key-a")
    _create_key(auth_client, "key-b")
    resp = auth_client.get("/v1/auth/keys")
    assert resp.status_code == 200
    names = [k["name"] for k in resp.json()]
    assert "key-a" in names
    assert "key-b" in names


def test_list_keys_does_not_expose_plaintext(auth_client: httpx.Client):
    _create_key(auth_client)
    resp = auth_client.get("/v1/auth/keys")
    for key in resp.json():
        assert "key" not in key  # plaintext field must not appear in list


def test_revoke_key(auth_client: httpx.Client):
    key_data = _create_key(auth_client)
    key_id = key_data["id"]
    plaintext = key_data["key"]

    resp = auth_client.delete(f"/v1/auth/keys/{key_id}")
    assert resp.status_code == 204

    # Key no longer in list
    listed = auth_client.get("/v1/auth/keys").json()
    assert all(k["id"] != key_id for k in listed)

    # Key no longer authenticates
    resp = auth_client.get("/v1/auth/me", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


def test_cannot_revoke_another_users_key(auth_client: httpx.Client, client: httpx.Client):
    """A different user's key cannot be deleted by the first user."""
    import uuid

    email = f"other-{uuid.uuid4().hex[:8]}@sheaf.dev"
    r = client.post("/v1/auth/register", json={"email": email, "password": "testpassword123"})
    other_token = r.json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    # Other user creates a key
    other_key_id = client.post(
        "/v1/auth/keys",
        json={"name": "other key", "scopes": ["members:read"]},
        headers=other_headers,
    ).json()["id"]

    # First user tries to delete it
    resp = auth_client.delete(f"/v1/auth/keys/{other_key_id}")
    assert resp.status_code == 404


def test_revoke_nonexistent_key_404(auth_client: httpx.Client):
    resp = auth_client.delete("/v1/auth/keys/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Authentication via API key
# ---------------------------------------------------------------------------

def test_api_key_authenticates(auth_client: httpx.Client):
    plaintext = _create_key(auth_client, scopes=["members:read"])["key"]
    with _key_client(plaintext) as c:
        resp = c.get("/v1/auth/me")
    assert resp.status_code == 200


def test_invalid_api_key_rejected(client: httpx.Client):
    resp = client.get("/v1/auth/me", headers={"Authorization": "Bearer sk_notavalidkey"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------

def test_key_with_read_scope_can_list_members(auth_client: httpx.Client):
    plaintext = _create_key(auth_client, scopes=["members:read"])["key"]
    with _key_client(plaintext) as c:
        resp = c.get("/v1/members")
    assert resp.status_code == 200


def test_key_with_read_scope_cannot_create_member(auth_client: httpx.Client):
    plaintext = _create_key(auth_client, scopes=["members:read"])["key"]
    with _key_client(plaintext) as c:
        resp = c.post("/v1/members", json={"name": "NoCreate"})
    assert resp.status_code == 403


def test_key_with_write_scope_can_create_member(auth_client: httpx.Client):
    plaintext = _create_key(auth_client, scopes=["members:write"])["key"]
    with _key_client(plaintext) as c:
        resp = c.post("/v1/members", json={"name": "ViaApiKey"})
    assert resp.status_code == 201


def test_write_scope_implies_read(auth_client: httpx.Client):
    """members:write alone should satisfy members:read endpoints."""
    plaintext = _create_key(auth_client, scopes=["members:write"])["key"]
    with _key_client(plaintext) as c:
        resp = c.get("/v1/members")
    assert resp.status_code == 200


def test_write_scope_does_not_imply_delete(auth_client: httpx.Client):
    """members:write must NOT grant members:delete."""
    member_id = auth_client.post("/v1/members", json={"name": "ToDelete"}).json()["id"]

    plaintext = _create_key(auth_client, scopes=["members:write"])["key"]
    with _key_client(plaintext) as c:
        resp = c.delete(f"/v1/members/{member_id}")
    assert resp.status_code == 403


def test_delete_scope_can_delete(auth_client: httpx.Client):
    member_id = auth_client.post("/v1/members", json={"name": "ToDelete"}).json()["id"]

    plaintext = _create_key(auth_client, scopes=["members:delete"])["key"]
    with _key_client(plaintext) as c:
        resp = c.delete(f"/v1/members/{member_id}")
    assert resp.status_code == 204


def test_key_with_no_scope_blocked(auth_client: httpx.Client):
    """A key with no scopes for a resource should be denied."""
    plaintext = _create_key(auth_client, scopes=["system:read"])["key"]
    with _key_client(plaintext) as c:
        resp = c.get("/v1/members")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Admin scope gating
# ---------------------------------------------------------------------------

def test_non_admin_cannot_create_admin_scoped_key(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/keys",
        json={"name": "hack", "scopes": ["admin:read"]},
    )
    assert resp.status_code == 403


def test_invalid_scope_name_rejected(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/keys",
        json={"name": "bad", "scopes": ["notascope:read"]},
    )
    assert resp.status_code == 422
