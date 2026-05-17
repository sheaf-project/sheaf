"""SimplyPlural import — preview endpoint.

The actual import runs asynchronously through the unified job runner
(POST /v1/imports/file). What's left here is the synchronous preview:
parse an SP export and return a summary of importable data. Preview
writes nothing.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from sheaf.auth.dependencies import get_current_user
from sheaf.models.user import User
from sheaf.schemas.sp_import import SPPreviewSummary
from sheaf.services.sp_import import preview

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024  # 100MB — SP exports can be large


async def _parse_upload(file: UploadFile) -> dict:
    """Read and parse an SP export JSON file."""
    data = await file.read()
    if len(data) > MAX_IMPORT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Import file too large. Max 100MB.",
        )
    try:
        return json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON file.",
        ) from exc


@router.post("/simplyplural/preview", response_model=SPPreviewSummary)
async def preview_import(
    file: UploadFile,
    _user: User = Depends(get_current_user),
):
    """Parse an SP export file and return a summary of importable data."""
    data = await _parse_upload(file)
    return preview(data)
