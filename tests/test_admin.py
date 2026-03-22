"""Tests for admin stats, user management, and maintenance endpoints."""

import os

import httpx

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


def _key_client(plaintext: str) -> httpx.Client:
    """Bare client authenticated only with the given API key."""
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {plaintext}"},
    )


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def test_stats_requires_admin(auth_client: httpx.Client):
    resp = auth_client.get("/v1/admin/stats")
    assert resp.status_code == 403


def test_stats_unauthenticated(client: httpx.Client):
    resp = client.get("/v1/admin/stats")
    assert resp.status_code in (401, 403)


def test_user_list_requires_admin(auth_client: httpx.Client):
    resp = auth_client.get("/v1/admin/users")
    assert resp.status_code == 403


def test_user_update_requires_admin(auth_client: httpx.Client):
    resp = auth_client.patch("/v1/admin/users/00000000-0000-0000-0000-000000000000", json={})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def test_stats_returns_expected_shape(admin_client: httpx.Client):
    resp = admin_client.get("/v1/admin/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_users" in data
    assert "total_members" in data
    assert "total_storage_bytes" in data
    assert "users_by_tier" in data
    assert data["total_users"] >= 1  # at least the admin user itself


def test_stats_counts_increase_after_registration(admin_client: httpx.Client, client: httpx.Client):
    import uuid
    before = admin_client.get("/v1/admin/stats").json()["total_users"]
    email = f"new-{uuid.uuid4().hex[:8]}@sheaf.dev"
    client.post("/v1/auth/register", json={"email": email, "password": "testpassword123"})
    after = admin_client.get("/v1/admin/stats").json()["total_users"]
    assert after == before + 1


# ---------------------------------------------------------------------------
# User list
# ---------------------------------------------------------------------------

def test_user_list_returns_users(admin_client: httpx.Client):
    resp = admin_client.get("/v1/admin/users")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    user = data[0]
    assert "id" in user
    assert "email" in user
    assert "tier" in user
    assert "is_admin" in user
    assert "member_count" in user
    assert "storage_used_bytes" in user


def test_user_list_admin_user_visible(admin_client: httpx.Client):
    me = admin_client.get("/v1/auth/me").json()
    users = admin_client.get("/v1/admin/users").json()
    ids = [u["id"] for u in users]
    assert me["id"] in ids


def test_user_list_search(admin_client: httpx.Client, client: httpx.Client):
    import uuid
    unique = uuid.uuid4().hex[:8]
    email = f"searchable-{unique}@sheaf.dev"
    client.post("/v1/auth/register", json={"email": email, "password": "testpassword123"})

    resp = admin_client.get(f"/v1/admin/users?search=searchable-{unique}")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) >= 1
    assert all("searchable" in u["email"] for u in results)


def test_user_list_pagination(admin_client: httpx.Client):
    resp = admin_client.get("/v1/admin/users?page=1&limit=1")
    assert resp.status_code == 200
    assert len(resp.json()) <= 1


# ---------------------------------------------------------------------------
# User update
# ---------------------------------------------------------------------------

def test_update_user_tier(admin_client: httpx.Client, auth_client: httpx.Client):
    me = auth_client.get("/v1/auth/me").json()
    user_id = me["id"]

    resp = admin_client.patch(f"/v1/admin/users/{user_id}", json={"tier": "plus"})
    assert resp.status_code == 200
    assert resp.json()["tier"] == "plus"

    # Revert
    admin_client.patch(f"/v1/admin/users/{user_id}", json={"tier": "free"})


def test_update_user_member_limit(admin_client: httpx.Client, auth_client: httpx.Client):
    user_id = auth_client.get("/v1/auth/me").json()["id"]

    resp = admin_client.patch(f"/v1/admin/users/{user_id}", json={"member_limit": 999})
    assert resp.status_code == 200
    assert resp.json()["member_limit"] == 999


def test_clear_member_limit(admin_client: httpx.Client, auth_client: httpx.Client):
    user_id = auth_client.get("/v1/auth/me").json()["id"]
    admin_client.patch(f"/v1/admin/users/{user_id}", json={"member_limit": 42})

    resp = admin_client.patch(f"/v1/admin/users/{user_id}", json={"clear_member_limit": True})
    assert resp.status_code == 200
    assert resp.json()["member_limit"] is None


def test_update_nonexistent_user(admin_client: httpx.Client):
    resp = admin_client.patch(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000",
        json={"tier": "plus"},
    )
    assert resp.status_code == 404


def test_is_admin_flag_updated(admin_client: httpx.Client, auth_client: httpx.Client):
    user_id = auth_client.get("/v1/auth/me").json()["id"]

    resp = admin_client.patch(f"/v1/admin/users/{user_id}", json={"is_admin": True})
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is True

    # Revert
    admin_client.patch(f"/v1/admin/users/{user_id}", json={"is_admin": False})


# ---------------------------------------------------------------------------
# Maintenance endpoints
# ---------------------------------------------------------------------------

def test_retention_run_requires_admin(auth_client: httpx.Client):
    resp = auth_client.post("/v1/admin/retention/run")
    assert resp.status_code == 403


def test_cleanup_run_requires_admin(auth_client: httpx.Client):
    resp = auth_client.post("/v1/admin/cleanup/run")
    assert resp.status_code == 403


def test_storage_audit_requires_admin(auth_client: httpx.Client):
    resp = auth_client.post("/v1/admin/storage/audit")
    assert resp.status_code == 403


def test_retention_run(admin_client: httpx.Client):
    resp = admin_client.post("/v1/admin/retention/run")
    assert resp.status_code == 200
    assert "pruned" in resp.json()


def test_cleanup_run(admin_client: httpx.Client):
    resp = admin_client.post("/v1/admin/cleanup/run")
    assert resp.status_code == 200
    data = resp.json()
    assert "users_checked" in data
    assert "total_orphaned" in data
    assert "total_freed_bytes" in data


def test_storage_audit_run(admin_client: httpx.Client):
    resp = admin_client.post("/v1/admin/storage/audit")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Admin API key scopes
# ---------------------------------------------------------------------------

def test_admin_api_key_can_access_stats(admin_client: httpx.Client):
    key = admin_client.post(
        "/v1/auth/keys",
        json={"name": "admin-read key", "scopes": ["admin:read"]},
    ).json()["key"]

    with _key_client(key) as c:
        resp = c.get("/v1/admin/stats")
    assert resp.status_code == 200


def test_admin_read_key_cannot_mutate(admin_client: httpx.Client, auth_client: httpx.Client):
    key = admin_client.post(
        "/v1/auth/keys",
        json={"name": "admin-read only", "scopes": ["admin:read"]},
    ).json()["key"]

    user_id = auth_client.get("/v1/auth/me").json()["id"]
    with _key_client(key) as c:
        resp = c.patch(f"/v1/admin/users/{user_id}", json={"tier": "plus"})
    assert resp.status_code == 403


def test_admin_write_key_can_mutate(admin_client: httpx.Client, auth_client: httpx.Client):
    key = admin_client.post(
        "/v1/auth/keys",
        json={"name": "admin-write key", "scopes": ["admin:write"]},
    ).json()["key"]

    user_id = auth_client.get("/v1/auth/me").json()["id"]
    with _key_client(key) as c:
        resp = c.patch(f"/v1/admin/users/{user_id}", json={"tier": "plus"})
    assert resp.status_code == 200

    # Revert
    admin_client.patch(f"/v1/admin/users/{user_id}", json={"tier": "free"})
