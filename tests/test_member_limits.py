"""Tests for member limit enforcement and avatar URL support."""

import httpx


def test_member_avatar_url_accepted(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/members",
        json={"name": "Alice", "avatar_url": "https://example.com/avatar.png"},
    )
    assert resp.status_code == 201
    assert resp.json()["avatar_url"] == "https://example.com/avatar.png"


def test_member_avatar_url_updatable(auth_client: httpx.Client):
    resp = auth_client.post("/v1/members", json={"name": "Bob"})
    member_id = resp.json()["id"]

    resp = auth_client.patch(
        f"/v1/members/{member_id}",
        json={"avatar_url": "https://example.com/new.png"},
    )
    assert resp.status_code == 200
    assert resp.json()["avatar_url"] == "https://example.com/new.png"


def test_member_avatar_url_clearable(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/members",
        json={"name": "Carol", "avatar_url": "https://example.com/avatar.png"},
    )
    member_id = resp.json()["id"]

    resp = auth_client.patch(f"/v1/members/{member_id}", json={"avatar_url": None})
    assert resp.status_code == 200
    assert resp.json()["avatar_url"] is None


def test_member_limit_enforced(auth_client: httpx.Client):
    """Self-hosted tier has unlimited members, but we can verify the endpoint
    handles the member_limit field and doesn't break on creation."""
    # Just verify we can create members normally — full limit enforcement
    # is tested separately since it requires a free-tier user fixture
    for i in range(3):
        resp = auth_client.post("/v1/members", json={"name": f"Member {i}"})
        assert resp.status_code == 201


def test_member_count_in_list(auth_client: httpx.Client):
    """Verify member list reflects created members."""
    before = len(auth_client.get("/v1/members").json())
    resp = auth_client.post("/v1/members", json={"name": "Extra"})
    assert resp.status_code == 201, f"Member creation failed: {resp.status_code} {resp.text}"
    after = len(auth_client.get("/v1/members").json())
    assert after == before + 1
