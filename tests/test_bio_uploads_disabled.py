"""Gating behaviour when the server has ALLOW_BIO_IMAGES=false.

Only runs under the `bio_uploads_disabled` config in run_tests.sh. Under that
config the master ALLOW_IMAGE_UPLOADS stays on, so avatars still upload —
only bio images are blocked.
"""
import base64
import io

import httpx
import pytest

pytestmark = pytest.mark.bio_uploads_disabled


def _png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def _upload(c: httpx.Client, purpose: str) -> httpx.Response:
    return c.post(
        f"/v1/files/upload?purpose={purpose}",
        files={"file": ("test.png", io.BytesIO(_png_bytes()), "image/png")},
    )


def test_me_reports_bio_uploads_not_allowed(auth_client: httpx.Client):
    me = auth_client.get("/v1/auth/me").json()
    assert me["uploads_allowed"] is True
    assert me["bio_uploads_allowed"] is False


def test_avatar_upload_still_works(auth_client: httpx.Client):
    resp = _upload(auth_client, "avatar")
    assert resp.status_code == 200


def test_bio_upload_rejected(auth_client: httpx.Client):
    resp = _upload(auth_client, "bio")
    assert resp.status_code == 403


def test_admin_can_bio_upload(admin_client: httpx.Client):
    assert admin_client.get("/v1/auth/me").json()["bio_uploads_allowed"] is True
    resp = _upload(admin_client, "bio")
    assert resp.status_code == 200


def test_allowlisted_user_can_bio_upload(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    user_id = auth_client.get("/v1/auth/me").json()["id"]
    admin_client.patch(
        f"/v1/admin/users/{user_id}", json={"can_upload_images": True}
    )

    assert auth_client.get("/v1/auth/me").json()["bio_uploads_allowed"] is True
    resp = _upload(auth_client, "bio")
    assert resp.status_code == 200
