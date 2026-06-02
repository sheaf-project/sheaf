"""Unit tests for sheaf.image_processing.

These run in-process without touching the live server. They cover the
core decode -> dim-cap -> EXIF strip -> re-encode pipeline plus the
animated-input flatten path and the decompression-bomb guard.
"""

import io
import struct
import zlib

import pytest
from PIL import Image

from sheaf.image_processing import (
    ImageNormalizationError,
    animation_allowed,
    normalize_image,
)


def _png(width: int, height: int, mode: str = "RGB") -> bytes:
    """Synthesize a valid Pillow PNG of the given dimensions."""
    img = Image.new(mode, (width, height), (128, 64, 32, 255)[: len(mode)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_with_exif() -> bytes:
    """Synthesize a JPEG that carries an EXIF block.

    Uses the Make/Model tags — plain strings, no rational quirks — and
    confirms the source actually has them via the assertion in the test.
    Real-world phone JPEGs carry these alongside the GPS tags we care
    about stripping.
    """
    img = Image.new("RGB", (32, 32), (200, 100, 50))
    exif = img.getexif()
    exif[0x010F] = "PhoneCo"          # Make
    exif[0x0110] = "Model-1"          # Model
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes(), quality=80)
    return buf.getvalue()


def _animated_gif(frames: int = 3) -> bytes:
    """Synthesize a multi-frame animated GIF."""
    images = []
    for i in range(frames):
        f = Image.new("RGB", (16, 16), (50 * i % 255, 100, 150))
        images.append(f)
    buf = io.BytesIO()
    images[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=100,
        loop=0,
    )
    return buf.getvalue()


def _png_header_claiming(width: int, height: int) -> bytes:
    """Forge a PNG header with the IHDR claiming huge dimensions.

    Pillow's lazy parser reads `image.size` from IHDR before touching
    pixel data, which is what the bomb-guard relies on. We rewrite the
    width/height fields in IHDR and recompute the chunk CRC so Pillow's
    PNG plugin doesn't bail on a checksum mismatch before we get a
    chance to bomb-guard.
    """
    real = _png(1, 1)
    arr = bytearray(real)
    # PNG layout: 8-byte signature, then IHDR = 4 len | 4 type | 13 data | 4 crc.
    # File offsets: type at 12, data at 16, crc at 29.
    struct.pack_into(">I", arr, 16, width)
    struct.pack_into(">I", arr, 20, height)
    new_crc = zlib.crc32(bytes(arr[12:29])) & 0xFFFFFFFF
    struct.pack_into(">I", arr, 29, new_crc)
    return bytes(arr)


# ---------------------------------------------------------------------------
# Static-input pipeline
# ---------------------------------------------------------------------------

def test_png_within_caps_passes_through():
    data = _png(64, 64)
    out, mime, animated = normalize_image(
        data,
        "image/png",
        allow_animation=False,
        max_dim=1024,
        max_frames=10,
        max_decoded_bytes=10 * 1024 * 1024,
    )
    assert mime == "image/png"
    assert animated is False
    # Output is re-encoded so won't be byte-identical, but must be a valid PNG.
    Image.open(io.BytesIO(out)).verify()


def test_oversized_png_gets_downscaled():
    data = _png(2048, 1024)
    out, mime, _ = normalize_image(
        data,
        "image/png",
        allow_animation=False,
        max_dim=512,
        max_frames=10,
        max_decoded_bytes=100 * 1024 * 1024,
    )
    img = Image.open(io.BytesIO(out))
    assert max(img.size) == 512
    # Aspect preserved (2:1).
    assert img.size == (512, 256)


def test_jpeg_exif_stripped():
    data = _jpeg_with_exif()
    # Sanity: the source must actually carry an EXIF block.
    src = Image.open(io.BytesIO(data))
    assert src.getexif()  # truthy non-empty

    out, mime, _ = normalize_image(
        data,
        "image/jpeg",
        allow_animation=False,
        max_dim=1024,
        max_frames=10,
        max_decoded_bytes=10 * 1024 * 1024,
    )
    assert mime == "image/jpeg"
    cleaned = Image.open(io.BytesIO(out))
    # EXIF should be either missing or empty after re-encode.
    assert not cleaned.getexif() or dict(cleaned.getexif()) == {}


# ---------------------------------------------------------------------------
# Bomb guard
# ---------------------------------------------------------------------------

def test_pixel_bomb_rejected_before_decode():
    # Forged dims fall in the gap between our app-side cap and Pillow's
    # own MAX_IMAGE_PIXELS guard (~178 megapixels by default), so we can
    # confirm OUR guard rejects without Pillow short-circuiting first.
    # 10000 x 3000 = 30 megapixels * 4 bytes/px = 120 MB > 100 MB cap.
    data = _png_header_claiming(10000, 3000)
    with pytest.raises(ImageNormalizationError) as ei:
        normalize_image(
            data,
            "image/png",
            allow_animation=False,
            max_dim=1024,
            max_frames=10,
            max_decoded_bytes=100 * 1024 * 1024,
        )
    assert "decoded-bytes cap" in str(ei.value)


def test_huge_pixel_bomb_caught_even_when_pillow_guard_fires_first():
    # Sanity check: an image bigger than Pillow's MAX_IMAGE_PIXELS is
    # still rejected by normalize_image — Pillow's guard raises and we
    # convert it to ImageNormalizationError. This complements the
    # previous test by confirming we don't accidentally pass through
    # the Pillow-side decompression-bomb exception.
    data = _png_header_claiming(50000, 50000)
    with pytest.raises(ImageNormalizationError):
        normalize_image(
            data,
            "image/png",
            allow_animation=False,
            max_dim=1024,
            max_frames=10,
            max_decoded_bytes=100 * 1024 * 1024,
        )


def test_garbage_bytes_rejected():
    with pytest.raises(ImageNormalizationError):
        normalize_image(
            b"this is not an image",
            "image/png",
            allow_animation=False,
            max_dim=1024,
            max_frames=10,
            max_decoded_bytes=10 * 1024 * 1024,
        )


# ---------------------------------------------------------------------------
# Animation paths
# ---------------------------------------------------------------------------

def test_animated_gif_flattened_when_gate_off():
    data = _animated_gif(frames=4)
    out, mime, animated = normalize_image(
        data,
        "image/gif",
        allow_animation=False,
        max_dim=1024,
        max_frames=10,
        max_decoded_bytes=10 * 1024 * 1024,
    )
    # was_animated reflects the SOURCE, not the output.
    assert animated is True
    # Gate closed -> static WebP output.
    assert mime == "image/webp"
    out_img = Image.open(io.BytesIO(out))
    # WebP that came in as a single-frame save has n_frames == 1.
    assert getattr(out_img, "n_frames", 1) == 1


def test_animated_gif_preserved_when_gate_on():
    data = _animated_gif(frames=4)
    out, mime, animated = normalize_image(
        data,
        "image/gif",
        allow_animation=True,
        max_dim=1024,
        max_frames=10,
        max_decoded_bytes=10 * 1024 * 1024,
    )
    assert mime == "image/gif"
    assert animated is True
    out_img = Image.open(io.BytesIO(out))
    assert getattr(out_img, "n_frames", 1) == 4


def test_animated_gif_exceeding_frame_cap_rejected_when_gate_on():
    data = _animated_gif(frames=8)
    with pytest.raises(ImageNormalizationError) as ei:
        normalize_image(
            data,
            "image/gif",
            allow_animation=True,
            max_dim=1024,
            max_frames=4,
            max_decoded_bytes=10 * 1024 * 1024,
        )
    assert "frames" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# animation_allowed policy
# ---------------------------------------------------------------------------

class _StubUser:
    """Minimal User stand-in: only the attrs animation_allowed reads."""

    def __init__(
        self,
        *,
        is_admin: bool = False,
        can_upload_animated_images: bool = False,
        tier: object = "free",
    ) -> None:
        self.is_admin = is_admin
        self.can_upload_animated_images = can_upload_animated_images
        self.tier = tier


class _StubSettings:
    def __init__(self, allow_animated_uploads: bool) -> None:
        self.allow_animated_uploads = allow_animated_uploads


def test_animation_allowed_master_switch_off_overrides_all():
    user = _StubUser(is_admin=True, can_upload_animated_images=True)
    settings = _StubSettings(allow_animated_uploads=False)
    assert animation_allowed(user, settings) is False  # type: ignore[arg-type]


def test_animation_allowed_admin_bypasses_when_master_on():
    user = _StubUser(is_admin=True)
    settings = _StubSettings(allow_animated_uploads=True)
    assert animation_allowed(user, settings) is True  # type: ignore[arg-type]


def test_animation_allowed_per_user_override():
    user = _StubUser(can_upload_animated_images=True)
    settings = _StubSettings(allow_animated_uploads=True)
    assert animation_allowed(user, settings) is True  # type: ignore[arg-type]


def test_animation_allowed_default_user_denied_with_empty_tier_set():
    # Default ANIMATED_AVATAR_TIERS is empty; a plain user without the
    # per-user flag should be denied even with the master switch on.
    user = _StubUser()
    settings = _StubSettings(allow_animated_uploads=True)
    assert animation_allowed(user, settings) is False  # type: ignore[arg-type]
