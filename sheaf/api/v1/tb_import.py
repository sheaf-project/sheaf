"""Tupperbox data import endpoints.

File-upload only: Tupperbox has no public API for third-party clients,
so a `tb!export` JSON dump is the only path. The shape is simple
enough that one preview + one import endpoint is all we need.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.tb_import import (
    TBImportOptions,
    TBImportResult,
    TBPreviewSummary,
)
from sheaf.services.tb_import import preview as build_preview
from sheaf.services.tb_import import run_import

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
        parsed = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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


async def _get_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if not system:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Create a system first.",
        )
    return system


def _options_from_query(
    member_ids: str | None,
    groups: bool,
) -> TBImportOptions:
    parsed_member_ids: list[str] | None = None
    if member_ids is not None:
        parsed_member_ids = [m.strip() for m in member_ids.split(",") if m.strip()]
    return TBImportOptions(
        member_ids=parsed_member_ids,
        groups=groups,
    )


@router.post("/tupperbox/preview", response_model=TBPreviewSummary)
async def preview_file_import(
    file: UploadFile,
    _user: User = Depends(get_current_user),
):
    """Parse a Tupperbox export file and return a summary."""
    data = await _parse_upload(file)
    return build_preview(data)


@router.post("/tupperbox", response_model=TBImportResult)
async def do_file_import(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    member_ids: str | None = None,
    groups: bool = True,
):
    """Import data from a Tupperbox export file.

    Use the preview endpoint first to see what's in the file and to
    gather IDs for the optional member_ids parameter (comma-separated).
    """
    data = await _parse_upload(file)
    system = await _get_system(user, db)
    options = _options_from_query(member_ids, groups)
    return await run_import(data, options, system, db)
