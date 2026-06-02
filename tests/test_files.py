import io

import httpx


def _png_bytes() -> bytes:
    """Minimal valid 1x1 PNG."""
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def _animated_gif_bytes(frames: int = 3) -> bytes:
    """Synthesize a small multi-frame animated GIF."""
    from PIL import Image
    images = [
        Image.new("RGB", (8, 8), (40 * i % 255, 80, 120))
        for i in range(frames)
    ]
    buf = io.BytesIO()
    images[0].save(
        buf, format="GIF", save_all=True,
        append_images=images[1:], duration=100, loop=0,
    )
    return buf.getvalue()


def _jpeg_with_exif_bytes() -> bytes:
    """Synthesize a JPEG carrying Make/Model EXIF tags."""
    from PIL import Image
    img = Image.new("RGB", (16, 16), (200, 100, 50))
    exif = img.getexif()
    exif[0x010F] = "PhoneCo"
    exif[0x0110] = "Model-1"
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes(), quality=80)
    return buf.getvalue()


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


def test_upload_rejects_fake_image_by_magic_bytes(auth_client: httpx.Client):
    """Header claims image/png but bytes are HTML — must reject on magic-byte sniff."""
    resp = auth_client.post(
        "/v1/files/upload",
        files={
            "file": (
                "evil.png",
                io.BytesIO(b"<html><script>alert(1)</script></html>" + b"\x00" * 64),
                "image/png",
            )
        },
    )
    assert resp.status_code == 400
    assert "match" in resp.json()["detail"].lower()


def test_upload_rejects_svg_posing_as_png(auth_client: httpx.Client):
    """SVG bytes with .png/image/png metadata — magic-byte sniff must reject."""
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    resp = auth_client.post(
        "/v1/files/upload",
        files={"file": ("logo.png", io.BytesIO(svg), "image/png")},
    )
    assert resp.status_code == 400


def test_upload_derives_extension_from_content_not_filename(auth_client: httpx.Client):
    """Even with a .html filename, the stored key must use the sniffed extension."""
    resp = auth_client.post(
        "/v1/files/upload",
        files={"file": ("xss.html", io.BytesIO(_png_bytes()), "image/png")},
    )
    assert resp.status_code == 200
    key = resp.json()["key"]
    assert key.endswith(".png")
    assert ".html" not in key


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


# ---------------------------------------------------------------------------
# Server-side normalization
# ---------------------------------------------------------------------------

def test_upload_response_includes_animated_field(auth_client: httpx.Client):
    """Every upload returns the `animated` field — false for a static PNG."""
    resp = auth_client.post(
        "/v1/files/upload",
        files={"file": ("test.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "animated" in data
    assert data["animated"] is False


def test_animated_gif_flattens_with_gate_closed(auth_client: httpx.Client):
    """Uploading an animated GIF without the animation entitlement flattens
    to a static WebP and surfaces animated=True so the client can tell
    the user we dropped the animation.
    """
    from PIL import Image
    resp = auth_client.post(
        "/v1/files/upload",
        files={
            "file": ("dance.gif", io.BytesIO(_animated_gif_bytes()), "image/gif"),
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["animated"] is True
    # Stored key must reflect the flattened-to-WebP container, not the
    # original .gif filename the client sent.
    assert data["key"].endswith(".webp")
    # Fetch back and confirm it's actually static.
    url = data["url"]
    if url.startswith("/"):
        served = auth_client.get(url)
        assert served.status_code == 200
        out_img = Image.open(io.BytesIO(served.content))
        assert getattr(out_img, "n_frames", 1) == 1


def test_jpeg_exif_stripped_on_upload(auth_client: httpx.Client):
    """Round-trip a JPEG with EXIF and confirm the stored copy has none."""
    from PIL import Image
    src_bytes = _jpeg_with_exif_bytes()
    # Sanity: source actually has EXIF.
    assert dict(Image.open(io.BytesIO(src_bytes)).getexif()), \
        "fixture is missing EXIF; test would be vacuous"

    resp = auth_client.post(
        "/v1/files/upload",
        files={"file": ("photo.jpg", io.BytesIO(src_bytes), "image/jpeg")},
    )
    assert resp.status_code == 200
    url = resp.json()["url"]
    if url.startswith("/"):
        served = auth_client.get(url)
        assert served.status_code == 200
        stored = Image.open(io.BytesIO(served.content))
        # Either no EXIF at all, or an empty IFD.
        assert not dict(stored.getexif())


def test_pixel_bomb_rejected_with_generic_message(auth_client: httpx.Client):
    """A forged PNG header claiming oversized dims is rejected as 400 with
    a generic message — Pillow internals must not leak."""
    import struct
    import zlib
    real = _png_bytes()
    arr = bytearray(real)
    struct.pack_into(">I", arr, 16, 10000)  # width
    struct.pack_into(">I", arr, 20, 3000)   # height (30 MP -> >100 MB cap)
    struct.pack_into(">I", arr, 29, zlib.crc32(bytes(arr[12:29])) & 0xFFFFFFFF)
    resp = auth_client.post(
        "/v1/files/upload",
        files={"file": ("bomb.png", io.BytesIO(bytes(arr)), "image/png")},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    # Generic phrasing only — no Pillow internals or pixel arithmetic.
    assert "process image" in detail
    assert "pillow" not in detail
    assert "decoded-bytes" not in detail


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


def test_me_reports_uploads_allowed_by_default(auth_client: httpx.Client):
    me = auth_client.get("/v1/auth/me").json()
    assert me["uploads_allowed"] is True
    assert me["bio_uploads_allowed"] is True
    assert me["external_images_allowed"] is True


def test_admin_can_set_can_upload_images(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    user_id = auth_client.get("/v1/auth/me").json()["id"]

    resp = admin_client.patch(
        f"/v1/admin/users/{user_id}", json={"can_upload_images": True}
    )
    assert resp.status_code == 200
    assert resp.json()["can_upload_images"] is True

    resp = admin_client.patch(
        f"/v1/admin/users/{user_id}", json={"can_upload_images": False}
    )
    assert resp.status_code == 200
    assert resp.json()["can_upload_images"] is False


def test_file_references_surfaces_member_avatar(auth_client: httpx.Client):
    """Selecting an uploaded file should show where it's referenced. An
    avatar attached to a member surfaces as a member_avatar reference."""
    up = auth_client.post(
        "/v1/files/upload",
        files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")},
    ).json()
    key = up["key"]
    files = auth_client.get("/v1/files/list").json()
    file_id = next(f["id"] for f in files if f["key"] == key)

    # Freshly uploaded, nothing points at it yet.
    refs0 = auth_client.get(f"/v1/files/{file_id}/references").json()
    assert refs0["references"] == [], refs0

    # Attach it as a member avatar (avatar_url stores the bare key).
    created = auth_client.post(
        "/v1/members", json={"name": "Pic Holder", "avatar_url": key}
    )
    assert created.status_code == 201, created.text

    refs1 = auth_client.get(f"/v1/files/{file_id}/references").json()
    kinds = {r["kind"] for r in refs1["references"]}
    assert "member_avatar" in kinds, refs1
    assert any("Pic Holder" in r["label"] for r in refs1["references"]), refs1


def test_file_references_404_for_unknown_file(auth_client: httpx.Client):
    resp = auth_client.get(
        "/v1/files/00000000-0000-0000-0000-000000000000/references"
    )
    assert resp.status_code == 404
