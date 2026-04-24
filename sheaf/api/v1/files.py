import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.auth.sessions import get_redis
from sheaf.config import settings
from sheaf.database import get_db
from sheaf.files import resolve_avatar_url, verify_file_token
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User, UserTier
from sheaf.services.file_cleanup import cleanup_orphaned_files
from sheaf.storage import get_storage

router = APIRouter(prefix="/files", tags=["files"])

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Canonical file extension for each validated image type. Used to build the
# stored key so the client-supplied filename can't smuggle an extension
# (e.g. .html, .svg) past the allow-list.
_MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _sniff_image_mime(data: bytes) -> str | None:
    """Identify the actual image format by magic bytes.

    Returns the canonical MIME type for JPEG/PNG/GIF/WebP, or None if the
    bytes don't match any supported format. Callers MUST use this rather
    than trusting the client-supplied Content-Type header.
    """
    if len(data) < 12:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


_QUOTA_MAP = {
    UserTier.FREE: lambda: settings.storage_quota_free_mb,
    UserTier.PLUS: lambda: settings.storage_quota_plus_mb,
    UserTier.SELF_HOSTED: lambda: settings.storage_quota_selfhosted_mb,
}


def _get_quota_bytes(user: User) -> int:
    """Return the storage quota in bytes for a user. 0 means unlimited."""
    mb = _QUOTA_MAP.get(user.tier, lambda: 0)()
    return mb * 1024 * 1024 if mb > 0 else 0


def _effective_size_limit_mb(purpose: str) -> int:
    """Per-purpose upload size cap (MB), falling back to max_upload_size_mb."""
    override = (
        settings.max_bio_image_size_mb
        if purpose == "bio"
        else settings.max_avatar_size_mb
    )
    return override if override > 0 else settings.max_upload_size_mb


@router.post(
    "/upload",
    dependencies=[Depends(require_scope("members:write")), rate_limit(10, 60, "user")],
)
async def upload_file(
    file: UploadFile,
    purpose: str = Query(default="avatar", pattern="^(avatar|bio)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not (user.is_admin or settings.allow_image_uploads or user.can_upload_images):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Image uploads are disabled on this instance.",
        )

    # Bio images have their own narrower toggle. Admins and per-user
    # allowlist still bypass, same as the master switch.
    if purpose == "bio" and not (
        user.is_admin or settings.allow_bio_images or user.can_upload_images
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bio images are disabled on this instance.",
        )

    # Cheap first-pass reject: client-supplied header must claim an allowed
    # type. The authoritative check is the magic-byte sniff below.
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {', '.join(ALLOWED_TYPES)}",
        )

    data = await file.read()
    file_size = len(data)

    max_mb = _effective_size_limit_mb(purpose)
    max_bytes = max_mb * 1024 * 1024
    if file_size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max: {max_mb}MB",
        )

    # Authoritative content check: magic bytes determine the real MIME type.
    # The client-supplied header and filename are NOT trusted past this point.
    actual_mime = _sniff_image_mime(data)
    if actual_mime is None or actual_mime not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content does not match a supported image format.",
        )

    # Cheap pre-check: reject obviously over-quota uploads before touching
    # storage. The authoritative check runs under a row lock below.
    quota = _get_quota_bytes(user)
    if quota > 0:
        used = await db.scalar(
            select(func.coalesce(func.sum(UploadedFile.size_bytes), 0))
            .where(UploadedFile.user_id == user.id)
        )
        if (used + file_size) > quota:
            quota_mb = quota // (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Storage quota exceeded. Limit: {quota_mb}MB",
            )

    # Server-derived extension + content-type from validated MIME. Never
    # trust file.filename or file.content_type for the stored object.
    ext = _MIME_EXT[actual_mime]
    prefix = "bios" if purpose == "bio" else "avatars"
    key = f"{prefix}/{user.id}/{uuid.uuid4().hex}.{ext}"

    storage = get_storage()
    await storage.put(key, data, actual_mime)

    # Serialize per-user quota accounting. Without this lock, two concurrent
    # uploads can both pass the quota check above and both insert, landing
    # the user over their limit. SELECT FOR UPDATE on the user row forces
    # the second uploader to wait until the first's transaction commits,
    # then recount with the new row visible.
    if quota > 0:
        await db.execute(
            select(User.id).where(User.id == user.id).with_for_update()
        )
        used = await db.scalar(
            select(func.coalesce(func.sum(UploadedFile.size_bytes), 0))
            .where(UploadedFile.user_id == user.id)
        )
        if (used + file_size) > quota:
            await storage.delete(key)
            await db.rollback()
            quota_mb = quota // (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Storage quota exceeded. Limit: {quota_mb}MB",
            )

    # Track upload
    db.add(UploadedFile(
        user_id=user.id,
        key=key,
        purpose=purpose,
        content_type=actual_mime,
        size_bytes=file_size,
    ))
    await db.commit()

    return {"url": resolve_avatar_url(key), "key": key, "size": file_size}


@router.get("/usage")
async def get_usage(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the user's current storage usage and quota."""
    result = await db.execute(
        select(
            func.coalesce(func.sum(UploadedFile.size_bytes), 0),
            func.count(UploadedFile.id),
        ).where(UploadedFile.user_id == user.id)
    )
    used_bytes, file_count = result.one()

    quota = _get_quota_bytes(user)
    return {
        "used_bytes": used_bytes,
        "quota_bytes": quota,  # 0 = unlimited
        "file_count": file_count,
    }


@router.get("/list")
async def list_files(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all uploaded files for the current user."""
    result = await db.execute(
        select(UploadedFile)
        .where(UploadedFile.user_id == user.id)
        .order_by(UploadedFile.created_at.desc())
    )
    files = result.scalars().all()
    return [
        {
            "id": str(f.id),
            "key": f.key,
            "url": resolve_avatar_url(f.key),
            "purpose": f.purpose,
            "content_type": f.content_type,
            "size_bytes": f.size_bytes,
            "created_at": f.created_at.isoformat(),
        }
        for f in files
    ]


@router.delete("/{file_id}", dependencies=[Depends(require_scope("members:write"))])
async def delete_file(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a specific uploaded file."""
    result = await db.execute(
        select(UploadedFile).where(
            UploadedFile.id == file_id,
            UploadedFile.user_id == user.id,
        )
    )
    file = result.scalar_one_or_none()
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    storage = get_storage()
    await storage.delete(file.key)
    await db.delete(file)
    await db.commit()
    return {"deleted": True, "key": file.key, "freed_bytes": file.size_bytes}


@router.post(
    "/cleanup",
    dependencies=[Depends(require_scope("members:write"))],
)
async def cleanup_files(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete orphaned files and reclaim storage quota."""
    result = await cleanup_orphaned_files(db, str(user.id))
    return result


@router.post(
    "/cleanup/dry-run",
    dependencies=[Depends(require_scope("members:write"))],
)
async def cleanup_dry_run(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview what would be cleaned up without deleting anything."""
    result = await cleanup_orphaned_files(db, str(user.id), dry_run=True)
    return result


serve_router = APIRouter(prefix="/files", tags=["files"])

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _s3_public_url(key: str) -> str:
    """Construct the direct public S3 URL for a key (unsigned mode, no CDN)."""
    if settings.s3_endpoint:
        return f"{settings.s3_endpoint}/{settings.s3_bucket}/{key}"
    return f"https://{settings.s3_bucket}.s3.{settings.s3_region}.amazonaws.com/{key}"


@serve_router.get("/{path:path}")
async def serve_file(
    path: str,
    token: str | None = Query(default=None),
    expires: str | None = Query(default=None),
):
    """Serve a file.

    Signed mode (default): requires a valid HMAC token + expiry query params.
      S3: redirects to a short-lived presigned URL (cached in Redis).
      Filesystem: serves bytes directly after token validation.

    Unsigned mode: no token required.
      S3: redirects to the direct public S3 URL (bucket must be public).
      Filesystem: serves bytes directly.

    CDN mode (S3 + s3_public_url): URLs bypass this endpoint entirely —
      resolve_avatar_url returns a CDN URL directly. This endpoint is not
      reached in normal operation for that case.
    """
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    if settings.image_serving == "signed" and (
        not token or not expires or not verify_file_token(path, token, expires)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired file URL",
        )

    storage = get_storage()

    if settings.storage_backend == "s3":
        if settings.image_serving == "signed":
            # Cache presigned URL in Redis, keyed by (path, expires) so it's
            # stable within the signing window — allows CDN/browser caching.
            redis_key = f"sheaf:file:presign:{path}:{expires}"
            r = await get_redis()
            presigned = await r.get(redis_key)
            if presigned is None:
                ttl = max(int(expires) - int(time.time()) - 60, 30)  # type: ignore[arg-type]
                presigned = await storage.presign(path, ttl + 60)  # type: ignore[attr-defined]
                await r.setex(redis_key, ttl, presigned)
            return RedirectResponse(url=presigned, status_code=307)
        else:
            # Unsigned: redirect to the public S3 URL (bucket must be public)
            return RedirectResponse(url=_s3_public_url(path), status_code=302)

    # Filesystem: serve bytes directly
    try:
        data = await storage.get(path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    content_type = _CONTENT_TYPES.get(suffix, "application/octet-stream")
    # Defence in depth: uploads are gated to image magic bytes so we should
    # never land here with a non-image extension. If we do (e.g. a legacy
    # file from before the validator was tightened), force a download
    # instead of letting the browser render.
    headers = (
        {"Content-Disposition": "attachment"}
        if content_type == "application/octet-stream"
        else {}
    )
    return Response(content=data, media_type=content_type, headers=headers)
