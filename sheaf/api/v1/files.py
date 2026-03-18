import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response

from sheaf.auth.dependencies import get_current_user
from sheaf.config import settings
from sheaf.models.user import User
from sheaf.storage import get_storage

router = APIRouter(prefix="/files", tags=["files"])

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    user: User = Depends(get_current_user),
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {', '.join(ALLOWED_TYPES)}",
        )

    data = await file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max: {settings.max_upload_size_mb}MB",
        )

    has_ext = file.filename and "." in file.filename
    ext = file.filename.rsplit(".", 1)[-1].lower() if has_ext else "bin"
    key = f"avatars/{user.id}/{uuid.uuid4().hex}.{ext}"

    storage = get_storage()
    url = await storage.put(key, data, file.content_type or "application/octet-stream")

    return {"url": url, "key": key}


@router.get("/{path:path}")
async def serve_file(path: str):
    """Serve files from filesystem storage. Not used when S3 backend is active."""
    if settings.storage_backend != "filesystem":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    storage = get_storage()
    data = await storage.get(path)
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
