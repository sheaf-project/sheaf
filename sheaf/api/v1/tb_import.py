"""Tupperbox import — preview endpoint.

The actual import runs asynchronously through the unified job runner
(POST /v1/imports/file). What's left here is the synchronous preview:
parse a `tb!export` JSON dump and return a summary so the user can
review + deselect tuppers before enqueueing. Preview writes nothing.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from sheaf.auth.dependencies import get_current_user
from sheaf.models.user import User
from sheaf.schemas.tb_import import TBPreviewSummary
from sheaf.services.import_parsing import ImportPayloadError, safe_json_loads_async
from sheaf.services.tb_import import preview as build_preview

logger = logging.getLogger("sheaf.import.tb")

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024  # 100MB — TB exports are tiny but be safe


async def _parse_upload(file: UploadFile) -> dict[str, Any]:
    """Read and parse a Tupperbox export JSON file from a multipart upload."""
    data = await file.read()
    if len(data) > MAX_IMPORT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Import file too large. Max 100MB.",
        )
    try:
        parsed = await safe_json_loads_async(data)
    except ImportPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON file.",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Export file does not contain a Tupperbox export object.",
        )
    return parsed


@router.post("/tupperbox/preview", response_model=TBPreviewSummary)
async def preview_file_import(
    file: UploadFile,
    _user: User = Depends(get_current_user),
):
    """Parse a Tupperbox export file and return a summary."""
    data = await _parse_upload(file)
    return build_preview(data)
