"""Gating behaviour when the server has ALLOW_EXTERNAL_IMAGES=false.

Only runs under the `external_images_disabled` config in run_tests.sh.
Under that config, uploads still work — only linking to third-party image
URLs (bio embeds + avatar URLs) is disabled.
"""
import base64
import io
import json
import uuid

import httpx
import pytest

from tests._import_runner_helpers import drive_import_runner, wait_for_terminal

pytestmark = pytest.mark.external_images_disabled


def _png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def test_me_reports_external_images_not_allowed(auth_client: httpx.Client):
    me = auth_client.get("/v1/auth/me").json()
    assert me["external_images_allowed"] is False
    # Uploads remain available.
    assert me["uploads_allowed"] is True


def test_external_avatar_url_is_dropped(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/members",
        json={"name": "ExtAvatar", "avatar_url": "https://example.com/a.png"},
    )
    assert resp.status_code == 201
    assert resp.json()["avatar_url"] is None


def test_hosted_avatar_key_still_accepted(auth_client: httpx.Client):
    upload = auth_client.post(
        "/v1/files/upload",
        files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    key = upload.json()["key"]
    resp = auth_client.post(
        "/v1/members", json={"name": "HostedAvatar", "avatar_url": key}
    )
    assert resp.status_code == 201
    # Server round-trips the key through resolve_avatar_url for display.
    assert resp.json()["avatar_url"] is not None


def test_external_bio_image_is_stripped(auth_client: httpx.Client):
    desc = "Hello ![evil](https://tracker.example.com/pixel.png) world"
    resp = auth_client.post(
        "/v1/members", json={"name": "ExtBio", "description": desc}
    )
    assert resp.status_code == 201
    stored = resp.json()["description"]
    assert "tracker.example.com" not in stored
    assert stored.startswith("Hello ")
    assert stored.endswith(" world")


def test_hosted_bio_image_still_accepted(auth_client: httpx.Client):
    upload = auth_client.post(
        "/v1/files/upload",
        files={"file": ("b.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    key = upload.json()["key"]
    desc = f"Check this out ![pic](/v1/files/{key})"
    resp = auth_client.post(
        "/v1/members", json={"name": "HostedBio", "description": desc}
    )
    assert resp.status_code == 201
    assert f"/v1/files/{key}" in resp.json()["description"]


def test_csp_blocks_https_images(auth_client: httpx.Client):
    resp = auth_client.get("/v1/auth/me")
    csp = resp.headers.get("Content-Security-Policy", "")
    # With external images disabled, img-src must not whitelist https:.
    assert "img-src" in csp
    img_directive = next(d for d in csp.split(";") if "img-src" in d)
    assert "https:" not in img_directive


def test_imported_external_avatar_urls_are_dropped(auth_client: httpx.Client):
    """Importers honour the instance external-image policy: a PK export
    carrying an external avatar URL imports the member without it."""
    payload = json.dumps(
        {"members": [{"name": "PolTest", "avatar_url": "https://example.com/a.png"}]}
    ).encode()
    resp = auth_client.post(
        "/v1/imports/file",
        files={"file": ("pk.json", payload, "application/json")},
        data={"source": "pluralkit_file", "idempotency_key": str(uuid.uuid4())},
    )
    assert resp.status_code == 202, resp.text
    drive_import_runner()
    final = wait_for_terminal(auth_client, resp.json()["id"])

    assert final["status"] == "complete", final
    members = auth_client.get("/v1/members").json()
    target = next(m for m in members if m["name"] == "PolTest")
    assert target["avatar_url"] is None, target
