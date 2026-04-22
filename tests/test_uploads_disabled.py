"""Gating behaviour when the server has ALLOW_IMAGE_UPLOADS=false.

Only runs under the `uploads_disabled` config in run_tests.sh, where the app
is started with the global flag disabled.
"""
import base64
import io

import httpx
import pytest

pytestmark = pytest.mark.uploads_disabled


def _png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def _upload(c: httpx.Client) -> httpx.Response:
    return c.post(
        "/v1/files/upload",
        files={"file": ("test.png", io.BytesIO(_png_bytes()), "image/png")},
    )


def test_me_reports_uploads_not_allowed(auth_client: httpx.Client):
    me = auth_client.get("/v1/auth/me").json()
    assert me["uploads_allowed"] is False
    # Master gate off implies bio gate off too.
    assert me["bio_uploads_allowed"] is False


def test_non_allowlisted_user_gets_403(auth_client: httpx.Client):
    resp = _upload(auth_client)
    assert resp.status_code == 403


def test_admin_can_upload_without_flag(admin_client: httpx.Client):
    assert admin_client.get("/v1/auth/me").json()["uploads_allowed"] is True
    resp = _upload(admin_client)
    assert resp.status_code == 200


def test_allowlisted_user_can_upload(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    user_id = auth_client.get("/v1/auth/me").json()["id"]
    admin_client.patch(
        f"/v1/admin/users/{user_id}", json={"can_upload_images": True}
    )

    # /auth/me now reflects the flip
    assert auth_client.get("/v1/auth/me").json()["uploads_allowed"] is True
    resp = _upload(auth_client)
    assert resp.status_code == 200
