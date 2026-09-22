"""BerryTree import - preview endpoint.

The actual import runs asynchronously through the unified job runner
(POST /v1/imports/file). What's left here is the synchronous preview:
parse a BerryTree export and return a summary of importable data. Preview
writes nothing.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.api.deps import get_user_system as _get_user_system
from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.user import User
from sheaf.schemas.berrytree_import import BTPreviewSummary
from sheaf.services.berrytree_import import preview
from sheaf.services.front_retention import front_retention_preview_warning
from sheaf.services.import_parsing import ImportPayloadError, safe_json_loads_async

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024  # 100MB, matching the other JSON importers



async def _parse_upload(file: UploadFile) -> dict:
    """Read and parse a BerryTree export JSON file."""
    data = await file.read()
    if len(data) > MAX_IMPORT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Import file too large. Max 100MB.",
        )
    try:
        return await safe_json_loads_async(data)
    except ImportPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON file.",
        ) from exc


@router.post("/berrytree/preview", response_model=BTPreviewSummary)
async def preview_import(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Parse a BerryTree export file and return a summary of importable data."""
    data = await _parse_upload(file)
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A BerryTree export should be a JSON object.",
        )
    summary = preview(data)
    system = await _get_user_system(user, db)
    retention_warning = front_retention_preview_warning(
        system.front_retention_days, summary.front_history_count > 0
    )
    if retention_warning:
        summary.limit_warnings.append(retention_warning)
    return summary
