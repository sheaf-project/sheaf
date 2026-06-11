"""Shared image-ingest pipeline for importers.

Importers that carry image bytes (PluralSpace zips, Prism envelopes,
Sheaf export-with-images archives) must push them through the same
pipeline the regular upload API uses: magic-byte sniff, Pillow
normalisation off the event loop (EXIF strip + dimension cap +
re-encode), the per-tier storage quota, and an `UploadedFile` row so
the blob is owned, quota-counted, and sweepable. Skipping any of those
turns an importer into an upload-policy bypass; this module is the one
copy so the next importer can't drift.

Callers keep their own user-facing warning wording: `store_imported_image`
raises `ImportImageError` with a machine-readable `reason`, and each
importer maps reasons to the phrasing its import-report already uses.
The instance/user "can images be uploaded at all" gate is also left to
callers (`user_can_upload_images`) since it's a one-per-import check,
not a per-image one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.image_processing import ImageNormalizationError, normalize_image
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User, UserTier
from sheaf.storage import get_storage

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def sniff_image_mime(data: bytes) -> str | None:
    """Magic-byte image format sniffer. Same predicates as the upload endpoint."""
    if len(data) < 12:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def user_can_upload_images(user: User) -> bool:
    return bool(
        user.is_admin or settings.allow_image_uploads or user.can_upload_images
    )


def animation_allowed(user: User) -> bool:
    """Mirror sheaf.files.animation_allowed for the importer context."""
    if not settings.allow_animated_uploads:
        return False
    if user.is_admin:
        return True
    return bool(getattr(user, "can_upload_animated_images", False))


def user_quota_bytes(user: User) -> int:
    quota_map = {
        UserTier.FREE: settings.storage_quota_free_mb,
        UserTier.PLUS: settings.storage_quota_plus_mb,
        UserTier.SELF_HOSTED: settings.storage_quota_selfhosted_mb,
    }
    mb = quota_map.get(user.tier, 0)
    return mb * 1024 * 1024 if mb > 0 else 0


class ImportImageError(Exception):
    """A single image couldn't be ingested.

    `reason` is machine-readable so callers can phrase their own
    warnings: "bad_format" | "normalize_rejected" | "quota_full".
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class StoredImportImage:
    key: str
    mime: str
    size: int
    # The pending UploadedFile row, so a caller that decides an upload
    # ended up unreferenced (e.g. the archive importer after a deduped
    # member skip) can remove it before commit.
    row: UploadedFile


async def store_imported_image(
    raw: bytes,
    *,
    db: AsyncSession,
    user: User,
    purpose: str,
) -> StoredImportImage:
    """Normalise + quota-check + persist one imported image blob.

    Mirrors the /v1/files/upload pipeline: sniff, normalize_image in a
    worker thread (Pillow decode is CPU-bound and must not block the
    event loop), tier storage quota, fresh storage key namespaced under
    the importing user, storage.put, UploadedFile row (added to the
    session, committed with the rest of the import's unit of work).

    `purpose` follows the upload API's vocabulary ("avatar" | "bio") and
    picks the key prefix the same way.

    Raises ImportImageError with a reason code on rejection; the caller
    owns the user-facing warning wording.
    """
    sniffed = sniff_image_mime(raw)
    if sniffed is None or sniffed not in ALLOWED_IMAGE_TYPES:
        raise ImportImageError("bad_format")

    try:
        normalised, mime, _was_animated = await run_in_threadpool(
            normalize_image,
            raw,
            sniffed,
            allow_animation=animation_allowed(user),
            max_dim=settings.max_image_dimension,
            max_frames=settings.max_animated_frames,
            max_decoded_bytes=settings.max_animated_decoded_bytes,
        )
    except ImageNormalizationError as exc:
        raise ImportImageError("normalize_rejected") from exc
    size = len(normalised)

    quota = user_quota_bytes(user)
    if quota > 0:
        used = await db.scalar(
            select(func.coalesce(func.sum(UploadedFile.size_bytes), 0)).where(
                UploadedFile.user_id == user.id
            )
        ) or 0
        if (used + size) > quota:
            raise ImportImageError("quota_full")

    prefix = "bios" if purpose == "bio" else "avatars"
    key = f"{prefix}/{user.id}/{uuid.uuid4().hex}.{MIME_EXT[mime]}"

    await get_storage().put(key, normalised, mime)
    row = UploadedFile(
        user_id=user.id,
        key=key,
        purpose=purpose,
        content_type=mime,
        size_bytes=size,
    )
    db.add(row)
    return StoredImportImage(key=key, mime=mime, size=size, row=row)
