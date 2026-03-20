import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.config import settings
from sheaf.database import get_db
from sheaf.models.user import User, UserTier
from sheaf.services.file_cleanup import cleanup_orphaned_files
from sheaf.storage import get_storage

router = APIRouter(prefix="/files", tags=["files"])

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

_QUOTA_MAP = {
    UserTier.FREE: lambda: settings.storage_quota_free_mb,
    UserTier.PLUS: lambda: settings.storage_quota_plus_mb,
    UserTier.SELF_HOSTED: lambda: settings.storage_quota_selfhosted_mb,
}


def _get_quota_bytes(user: User) -> int:
    """Return the storage quota in bytes for a user. 0 means unlimited."""
    mb = _QUOTA_MAP.get(user.tier, lambda: 0)()
    return mb * 1024 * 1024 if mb > 0 else 0


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {', '.join(ALLOWED_TYPES)}",
        )

    data = await file.read()
    file_size = len(data)

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if file_size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max: {settings.max_upload_size_mb}MB",
        )

    # Check storage quota
    quota = _get_quota_bytes(user)
    if quota > 0 and (user.storage_used_bytes + file_size) > quota:
        quota_mb = quota // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Storage quota exceeded. Limit: {quota_mb}MB",
        )

    has_ext = file.filename and "." in file.filename
    ext = file.filename.rsplit(".", 1)[-1].lower() if has_ext else "bin"
    key = f"avatars/{user.id}/{uuid.uuid4().hex}.{ext}"

    storage = get_storage()
    url = await storage.put(key, data, file.content_type or "application/octet-stream")

    # Track usage
    user.storage_used_bytes += file_size

    return {"url": url, "key": key, "size": file_size}


@router.get("/usage")
async def get_usage(user: User = Depends(get_current_user)):
    """Return the user's current storage usage and quota."""
    quota = _get_quota_bytes(user)
    return {
        "used_bytes": user.storage_used_bytes,
        "quota_bytes": quota,  # 0 = unlimited
    }


@router.post("/cleanup")
async def cleanup_files(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete orphaned files and reclaim storage quota."""
    result = await cleanup_orphaned_files(db, str(user.id))
    return result


@router.post("/cleanup/dry-run")
async def cleanup_dry_run(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview what would be cleaned up without deleting anything."""
    result = await cleanup_orphaned_files(db, str(user.id), dry_run=True)
    return result


@router.get("/serve/{path:path}")
async def serve_file(path: str):
    """Serve files from filesystem storage. Not used when S3 backend is active."""
    if settings.storage_backend != "filesystem":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Reject path traversal at the API layer
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    storage = get_storage()
    try:
        data = await storage.get(path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Infer content type from extension
    content_type = "application/octet-stream"
    if path.endswith(".jpg") or path.endswith(".jpeg"):
        content_type = "image/jpeg"
    elif path.endswith(".png"):
        content_type = "image/png"
    elif path.endswith(".gif"):
        content_type = "image/gif"
    elif path.endswith(".webp"):
        content_type = "image/webp"

    return Response(content=data, media_type=content_type)
