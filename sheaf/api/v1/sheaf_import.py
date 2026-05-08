"""Sheaf data import endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.services.sheaf_import import preview, run_import

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024  # 100MB


async def _parse_upload(file: UploadFile) -> dict:
    """Read and parse a Sheaf export JSON file."""
    data = await file.read()
    if len(data) > MAX_IMPORT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Import file too large. Max 100MB.",
        )
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON file.",
        ) from exc

    # Accept both legacy v1 exports and current v2 exports. v2 added new
    # top-level keys (reminders, watch_tokens, polls, journals, revisions,
    # uploaded_files) over time; the importer only consumes the original
    # set (system / members / fronts / groups / tags / custom_fields) and
    # silently ignores extras, so accepting v2 is forward-compatible
    # without requiring per-field handlers for the not-yet-importable
    # surfaces. New format versions should be added here as they ship.
    if (
        not isinstance(parsed, dict)
        or parsed.get("version") not in {"1", "2"}
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Not a valid Sheaf export file (missing or unsupported "
                "version field — expected 1 or 2)."
            ),
        )
    return parsed


async def _get_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if not system:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Create a system first.",
        )
    return system


@router.post("/sheaf/preview")
async def preview_import(
    file: UploadFile,
    _user: User = Depends(get_current_user),
):
    """Parse a Sheaf export file and return a summary of importable data."""
    data = await _parse_upload(file)
    p = preview(data)
    return {
        "system_name": p.system_name,
        "member_count": p.member_count,
        "members": p.members,
        "front_count": p.front_count,
        "group_count": p.group_count,
        "tag_count": p.tag_count,
        "custom_field_count": p.custom_field_count,
    }


@router.post("/sheaf")
async def do_import(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    system_profile: bool = True,
    member_ids: str | None = None,
    fronts: bool = True,
    groups: bool = True,
    tags: bool = True,
    custom_fields: bool = True,
):
    """Import data from a Sheaf export file.

    Upload the JSON export file with query parameters controlling what to import.
    Use the preview endpoint first to see what's in the file and get member IDs
    for selective import.
    """
    data = await _parse_upload(file)
    system = await _get_system(user, db)

    parsed_member_ids = None
    if member_ids is not None:
        parsed_member_ids = [mid.strip() for mid in member_ids.split(",") if mid.strip()]

    result = await run_import(
        data,
        system,
        db,
        system_profile=system_profile,
        member_ids=parsed_member_ids,
        fronts=fronts,
        groups=groups,
        tags=tags,
        custom_fields=custom_fields,
    )

    return {
        "members_imported": result.members_imported,
        "fronts_imported": result.fronts_imported,
        "groups_imported": result.groups_imported,
        "tags_imported": result.tags_imported,
        "custom_fields_imported": result.custom_fields_imported,
        "warnings": result.warnings,
    }
