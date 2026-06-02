"""Image normalization and animation-eligibility policy.

The /v1/files/upload endpoint runs every accepted image through
`normalize_image` after the magic-byte sniff. Normalization:

  - Guards against decompression bombs by checking declared dimensions
    against `max_decoded_bytes` BEFORE asking Pillow to load pixel data.
  - Caps the longest edge to `max_dim`, preserving aspect ratio.
  - Strips EXIF, ICC, and other metadata chunks (phone JPEGs commonly
    carry GPS).
  - Re-encodes through Pillow so polyglot / parser-tricked containers
    are canonicalised out.

Animated GIF / animated WebP handling depends on the `allow_animation`
flag passed in. When False (today's default), animated input collapses
to its first frame and is re-encoded as static WebP. When True, frames
are iterated and re-encoded preserving animation, subject to the
`max_frames` cap.

The animation decision itself lives in `animation_allowed()`, called at
the upload endpoint. It combines the master `allow_animated_uploads`
setting, an `is_admin` bypass, a per-user `can_upload_animated_images`
override, and a tier-based eligibility set. The tier set is empty
today; populate `ANIMATED_AVATAR_TIERS` when premium gating lands.
"""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageSequence, UnidentifiedImageError

from sheaf.config import Settings
from sheaf.models.user import User, UserTier

logger = logging.getLogger(__name__)

# Tiers permitted to upload animated avatars. Empty today (premium gate
# not live yet). Flip to e.g. {UserTier.PLUS, UserTier.SELF_HOSTED} when
# the feature opens up. The animation_allowed() helper short-circuits to
# False when this is empty AND the per-user override is unset, so the
# code path is safe to leave wired up.
ANIMATED_AVATAR_TIERS: set[UserTier] = set()


class ImageNormalizationError(Exception):
    """Raised when an uploaded image cannot be safely normalized.

    The endpoint converts this to a 400 with a generic message; the
    exception text is for logs only and is never echoed to the client,
    so it can carry parser detail without becoming an info leak.
    """


def animation_allowed(user: User, settings: Settings) -> bool:
    """Decide whether `user` may upload animated avatars on this instance.

    Order:
      1. Master switch off -> always False (defence in depth: even if a
         user override slipped in, the operator-level kill switch wins).
      2. Admin bypass.
      3. Per-user override.
      4. Tier eligibility.
    """
    if not settings.allow_animated_uploads:
        return False
    if user.is_admin:
        return True
    if user.can_upload_animated_images:
        return True
    return user.tier in ANIMATED_AVATAR_TIERS


# Map sniffed input MIME to output MIME + Pillow format. The animated-gate
# off path overrides image/gif -> image/webp because the source was an
# animation and we want to drop the animation but keep the smaller size.
_STATIC_OUTPUT = {
    "image/jpeg": ("image/jpeg", "JPEG"),
    "image/png": ("image/png", "PNG"),
    "image/gif": ("image/gif", "GIF"),
    "image/webp": ("image/webp", "WEBP"),
}


def _bomb_guard(img: Image.Image, max_decoded_bytes: int) -> None:
    """Reject images whose declared pixel area would exceed the cap.

    Runs against `img.size` which Pillow reads from the container header
    without decoding the image data. The 4 bytes/pixel constant is a
    safe overestimate for any RGBA-or-smaller mode we accept.
    """
    width, height = img.size
    declared = width * height * 4
    if declared > max_decoded_bytes:
        raise ImageNormalizationError(
            f"declared image area exceeds decoded-bytes cap: "
            f"{width}x{height} -> {declared} > {max_decoded_bytes}"
        )


def _cap_dim(img: Image.Image, max_dim: int) -> Image.Image:
    width, height = img.size
    if width <= max_dim and height <= max_dim:
        return img
    if width >= height:
        new_w = max_dim
        new_h = max(1, round(height * max_dim / width))
    else:
        new_h = max_dim
        new_w = max(1, round(width * max_dim / height))
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def _encode_static(img: Image.Image, fmt: str) -> bytes:
    """Re-encode `img` as a single static frame, EXIF stripped.

    Pillow's `Image.save` only writes the data it's given; constructing
    a fresh `Image.new` from the decoded pixels guarantees no carryover
    of the source's info dict (which is where EXIF lives).
    """
    if img.mode not in ("RGB", "RGBA"):
        # Quantised palette images (GIF P-mode) and L (greyscale) get
        # promoted so the re-encoder has predictable input.
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    clean = Image.new(img.mode, img.size)
    clean.paste(img)

    buf = io.BytesIO()
    if fmt == "JPEG":
        if clean.mode == "RGBA":
            # JPEG has no alpha; flatten onto white before encoding so the
            # result isn't black-on-transparent confetti.
            bg = Image.new("RGB", clean.size, (255, 255, 255))
            bg.paste(clean, mask=clean.split()[-1])
            clean = bg
        clean.save(buf, format="JPEG", quality=90, subsampling=0, optimize=True)
    elif fmt == "PNG":
        clean.save(buf, format="PNG", optimize=True)
    elif fmt == "WEBP":
        clean.save(buf, format="WEBP", quality=90, method=6)
    elif fmt == "GIF":
        # Single-frame GIF: only reachable on the animation-allowed path
        # with a one-frame source. Preserve format symmetry.
        clean.save(buf, format="GIF")
    else:
        raise ImageNormalizationError(f"unknown output format: {fmt}")
    return buf.getvalue()


def _encode_animated(
    img: Image.Image,
    fmt: str,
    *,
    max_dim: int,
    max_frames: int,
) -> bytes:
    """Re-encode an animated image preserving all frames.

    Frame count is bounded by `max_frames`; per-frame dim cap applies.
    Frame durations are read from each frame's info dict and replayed
    on the output.
    """
    frames: list[Image.Image] = []
    durations: list[int] = []
    for i, frame in enumerate(ImageSequence.Iterator(img)):
        if i >= max_frames:
            raise ImageNormalizationError(
                f"animated input has more than {max_frames} frames"
            )
        clean = frame.convert("RGBA")
        clean = _cap_dim(clean, max_dim)
        frames.append(clean)
        durations.append(int(frame.info.get("duration", 100)))

    if not frames:
        raise ImageNormalizationError("animated input produced no frames")

    buf = io.BytesIO()
    head, *tail = frames
    save_kwargs: dict[str, object] = {
        "format": fmt,
        "save_all": True,
        "append_images": tail,
        "duration": durations,
        "loop": int(img.info.get("loop", 0)),
        "disposal": 2,
    }
    if fmt == "WEBP":
        save_kwargs["quality"] = 90
        save_kwargs["method"] = 6
    head.save(buf, **save_kwargs)
    return buf.getvalue()


def normalize_image(
    data: bytes,
    sniffed_mime: str,
    *,
    allow_animation: bool,
    max_dim: int,
    max_frames: int,
    max_decoded_bytes: int,
) -> tuple[bytes, str, bool]:
    """Decode, validate, re-encode an uploaded image.

    Returns (new_bytes, new_mime, was_animated). `was_animated` is True
    when the source had more than one frame, regardless of whether the
    output kept the animation - callers use it to tell the user "we
    flattened your GIF" when the gate is closed.

    Raises ImageNormalizationError on any decode, dimension, or frame
    failure. The caller is responsible for converting to a 400 with a
    generic user-facing message.
    """
    try:
        img = Image.open(io.BytesIO(data))
    except UnidentifiedImageError as e:
        raise ImageNormalizationError(f"unidentified image: {e}") from e
    except Exception as e:
        raise ImageNormalizationError(f"image open failed: {e}") from e

    _bomb_guard(img, max_decoded_bytes)

    # n_frames is only defined for multi-frame formats. Pillow lazily
    # parses the index when accessed, so it's the cheapest "is this
    # animated?" check.
    try:
        n_frames = getattr(img, "n_frames", 1)
    except Exception:
        n_frames = 1
    was_animated = n_frames > 1

    if sniffed_mime not in _STATIC_OUTPUT:
        raise ImageNormalizationError(f"unsupported sniffed mime: {sniffed_mime}")

    try:
        if was_animated and allow_animation:
            # Preserve the source container so a GIF stays a GIF (and a
            # WebP stays a WebP). Static-source containers can't reach
            # this branch — n_frames would be 1.
            out_mime, fmt = _STATIC_OUTPUT[sniffed_mime]
            new_bytes = _encode_animated(
                img, fmt, max_dim=max_dim, max_frames=max_frames,
            )
            return new_bytes, out_mime, was_animated

        if was_animated and not allow_animation:
            # Drop animation, re-encode first frame as static WebP. WebP
            # because it's smaller than PNG and preserves alpha if the
            # source had it.
            first = next(ImageSequence.Iterator(img)).copy()
            first = _cap_dim(first, max_dim)
            new_bytes = _encode_static(first, "WEBP")
            return new_bytes, "image/webp", was_animated

        # Static input: keep source container.
        out_mime, fmt = _STATIC_OUTPUT[sniffed_mime]
        img = _cap_dim(img, max_dim)
        new_bytes = _encode_static(img, fmt)
        return new_bytes, out_mime, was_animated
    except ImageNormalizationError:
        raise
    except Exception as e:
        raise ImageNormalizationError(f"re-encode failed: {e}") from e
