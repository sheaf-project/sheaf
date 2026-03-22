import io

import httpx


def _png_bytes() -> bytes:
    """Minimal valid 1x1 PNG."""
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def test_upload_image(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/files/upload",
        files={"file": ("test.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "url" in data
    assert "key" in data
    assert data["size"] > 0


def test_upload_rejects_non_image(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/files/upload",
        files={"file": ("shell.sh", io.BytesIO(b"#!/bin/bash\necho hi"), "text/x-shellscript")},
    )
    assert resp.status_code == 400


def test_upload_unauthenticated(client: httpx.Client):
    resp = client.post(
        "/v1/files/upload",
        files={"file": ("test.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    assert resp.status_code in (401, 403)


def test_storage_usage_returns_data(auth_client: httpx.Client):
    resp = auth_client.get("/v1/files/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert "used_bytes" in data
    assert "quota_bytes" in data
    assert "file_count" in data
    assert data["used_bytes"] >= 0


def test_storage_usage_reflects_upload(auth_client: httpx.Client):
    before = auth_client.get("/v1/files/usage").json()["file_count"]
    auth_client.post(
        "/v1/files/upload",
        files={"file": ("test.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    # Cache is invalidated on upload, so usage should update immediately
    after = auth_client.get("/v1/files/usage").json()["file_count"]
    assert after == before + 1


def test_served_file_is_accessible(auth_client: httpx.Client):
    upload = auth_client.post(
        "/v1/files/upload",
        files={"file": ("test.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    url = upload.json()["url"]
    # Filesystem URLs are relative; S3 URLs are absolute — only test relative ones
    if url.startswith("/"):
        resp = auth_client.get(url)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"


def test_cleanup_dry_run(auth_client: httpx.Client):
    resp = auth_client.post("/v1/files/cleanup/dry-run")
    assert resp.status_code == 200
    data = resp.json()
    assert "orphaned" in data
    assert data["dry_run"] is True


def test_cleanup(auth_client: httpx.Client):
    resp = auth_client.post("/v1/files/cleanup")
    assert resp.status_code == 200
    data = resp.json()
    assert "orphaned" in data
    assert "freed_bytes" in data
