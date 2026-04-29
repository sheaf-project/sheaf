"""Tests for server announcements CRUD and public endpoint."""

import httpx

# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def test_admin_announcements_requires_admin(auth_client: httpx.Client):
    resp = auth_client.get("/v1/admin/announcements")
    assert resp.status_code == 403


def test_create_announcement_requires_admin(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/admin/announcements",
        json={"title": "Test", "body": "Body"},
    )
    assert resp.status_code == 403


def test_public_announcements_requires_auth(client: httpx.Client):
    resp = client.get("/v1/announcements")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Admin CRUD
# ---------------------------------------------------------------------------


def test_create_and_list_announcement(admin_client: httpx.Client):
    resp = admin_client.post(
        "/v1/admin/announcements",
        json={
            "title": "Maintenance Window",
            "body": "Scheduled downtime at 2am UTC.",
            "severity": "warning",
            "dismissible": True,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Maintenance Window"
    assert data["severity"] == "warning"
    assert data["dismissible"] is True
    assert data["active"] is True
    assert "id" in data
    assert "created_by" in data  # admin endpoint includes this

    # List includes the new announcement
    list_resp = admin_client.get("/v1/admin/announcements")
    assert list_resp.status_code == 200
    titles = [a["title"] for a in list_resp.json()]
    assert "Maintenance Window" in titles


def test_update_announcement(admin_client: httpx.Client):
    create = admin_client.post(
        "/v1/admin/announcements",
        json={"title": "Old Title", "body": "Old body"},
    )
    assert create.status_code == 201
    ann_id = create.json()["id"]

    patch = admin_client.patch(
        f"/v1/admin/announcements/{ann_id}",
        json={"title": "New Title", "body": "New body", "severity": "critical"},
    )
    assert patch.status_code == 200
    assert patch.json()["title"] == "New Title"
    assert patch.json()["severity"] == "critical"


def test_deactivate_announcement(admin_client: httpx.Client):
    create = admin_client.post(
        "/v1/admin/announcements",
        json={"title": "Will Deactivate", "body": "Body"},
    )
    ann_id = create.json()["id"]

    patch = admin_client.patch(
        f"/v1/admin/announcements/{ann_id}",
        json={"active": False},
    )
    assert patch.status_code == 200
    assert patch.json()["active"] is False


def test_delete_announcement(admin_client: httpx.Client):
    create = admin_client.post(
        "/v1/admin/announcements",
        json={"title": "Will Delete", "body": "Body"},
    )
    ann_id = create.json()["id"]

    delete = admin_client.delete(f"/v1/admin/announcements/{ann_id}")
    assert delete.status_code == 204

    # Verify it's gone
    get = admin_client.patch(
        f"/v1/admin/announcements/{ann_id}",
        json={"title": "Ghost"},
    )
    assert get.status_code == 404


def test_delete_nonexistent_announcement(admin_client: httpx.Client):
    resp = admin_client.delete(
        "/v1/admin/announcements/00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404


def test_create_invalid_severity(admin_client: httpx.Client):
    resp = admin_client.post(
        "/v1/admin/announcements",
        json={"title": "Bad", "body": "Body", "severity": "extreme"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Public endpoint
# ---------------------------------------------------------------------------


def test_public_endpoint_returns_active_only(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    # Create one active and one inactive
    admin_client.post(
        "/v1/admin/announcements",
        json={"title": "Active Ann", "body": "Visible", "severity": "info"},
    )
    create2 = admin_client.post(
        "/v1/admin/announcements",
        json={"title": "Inactive Ann", "body": "Hidden", "active": False},
    )
    assert create2.status_code == 201

    resp = auth_client.get("/v1/announcements")
    assert resp.status_code == 200
    titles = [a["title"] for a in resp.json()]
    assert "Active Ann" in titles
    assert "Inactive Ann" not in titles


def test_public_endpoint_excludes_admin_fields(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    admin_client.post(
        "/v1/admin/announcements",
        json={"title": "Public Check", "body": "Body"},
    )

    resp = auth_client.get("/v1/announcements")
    assert resp.status_code == 200
    for ann in resp.json():
        if ann["title"] == "Public Check":
            # Public schema must NOT include admin-only fields
            assert "created_by" not in ann
            assert "updated_at" not in ann
            assert "active" not in ann
            break


def test_non_dismissible_announcement(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    admin_client.post(
        "/v1/admin/announcements",
        json={
            "title": "Important",
            "body": "Cannot dismiss",
            "dismissible": False,
            "severity": "critical",
        },
    )

    resp = auth_client.get("/v1/announcements")
    assert resp.status_code == 200
    for ann in resp.json():
        if ann["title"] == "Important":
            assert ann["dismissible"] is False
            assert ann["severity"] == "critical"
            break


# ---------------------------------------------------------------------------
# Logged-out (unauthenticated) public endpoint
# ---------------------------------------------------------------------------


def test_logged_out_endpoint_no_auth_required(client: httpx.Client):
    resp = client.get("/v1/announcements/public")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_logged_out_endpoint_filters_by_flag(
    admin_client: httpx.Client, client: httpx.Client
):
    admin_client.post(
        "/v1/admin/announcements",
        json={
            "title": "Login-Page Banner",
            "body": "Visible while logged out",
            "visible_while_logged_out": True,
        },
    )
    admin_client.post(
        "/v1/admin/announcements",
        json={
            "title": "Internal Only",
            "body": "Authenticated users only",
        },
    )

    resp = client.get("/v1/announcements/public")
    assert resp.status_code == 200
    titles = [a["title"] for a in resp.json()]
    assert "Login-Page Banner" in titles
    assert "Internal Only" not in titles


def test_logged_out_endpoint_excludes_inactive(
    admin_client: httpx.Client, client: httpx.Client
):
    create = admin_client.post(
        "/v1/admin/announcements",
        json={
            "title": "Hidden Login Banner",
            "body": "inactive",
            "visible_while_logged_out": True,
            "active": False,
        },
    )
    assert create.status_code == 201

    resp = client.get("/v1/announcements/public")
    assert resp.status_code == 200
    titles = [a["title"] for a in resp.json()]
    assert "Hidden Login Banner" not in titles


def test_admin_create_persists_visible_while_logged_out(admin_client: httpx.Client):
    resp = admin_client.post(
        "/v1/admin/announcements",
        json={
            "title": "Flagged",
            "body": "Body",
            "visible_while_logged_out": True,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["visible_while_logged_out"] is True


def test_admin_update_visible_while_logged_out(admin_client: httpx.Client):
    create = admin_client.post(
        "/v1/admin/announcements",
        json={"title": "Toggle Me", "body": "Body"},
    )
    ann_id = create.json()["id"]
    assert create.json()["visible_while_logged_out"] is False

    patch = admin_client.patch(
        f"/v1/admin/announcements/{ann_id}",
        json={"visible_while_logged_out": True},
    )
    assert patch.status_code == 200
    assert patch.json()["visible_while_logged_out"] is True
