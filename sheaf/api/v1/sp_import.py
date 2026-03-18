"""SimplyPlural data import endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.sp_import import SPImportOptions, SPImportResult, SPPreviewSummary
from sheaf.services.sp_import import preview, run_import

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


async def _get_system(user: User, db: AsyncSession) -> System:
    """Get the user's system, or 404."""
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if not system:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Create a system first.",
        )
    return system


@router.post("/simplyplural/preview", response_model=SPPreviewSummary)
async def preview_import(
    file: UploadFile,
    _user: User = Depends(get_current_user),
):
    """Parse an SP export file and return a summary of importable data."""
    data = await _parse_upload(file)
    return preview(data)


@router.post("/simplyplural", response_model=SPImportResult)
async def do_import(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    system_profile: bool = True,
    member_ids: str | None = None,  # Comma-separated SP member IDs, or omit for all
    custom_fronts: bool = True,
    custom_fields: bool = True,
    groups: bool = True,
    front_history: bool = False,
    notes: bool = False,
):
    """Import data from a SimplyPlural export file.

    Upload the JSON export file with query parameters controlling what to import.
    Use the preview endpoint first to see what's in the file and get member IDs
    for selective import.
    """
    data = await _parse_upload(file)
    system = await _get_system(user, db)

    parsed_member_ids = None
    if member_ids is not None:
        parsed_member_ids = [mid.strip() for mid in member_ids.split(",") if mid.strip()]

    options = SPImportOptions(
        system_profile=system_profile,
        member_ids=parsed_member_ids,
        custom_fronts=custom_fronts,
        custom_fields=custom_fields,
        groups=groups,
        front_history=front_history,
        notes=notes,
    )

    return await run_import(data, options, system, db)
