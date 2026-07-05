"""PluralSpace import — preview endpoint.

The actual import runs through the async job runner (POST
/v1/imports/file with source=pluralspace_file). What's left here is
the synchronous preview: open the zip, count entities, return a
summary the user can confirm before enqueueing.
"""

from __future__ import annotations

import zipfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.services.front_retention import front_retention_preview_warning
from sheaf.services.import_parsing import ImportPayloadError
from sheaf.services.pluralspace_import import parse_export_async, preview
from sheaf.services.sheaf_import import open_poll_preview_warning

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


# Per-user rate limit: a worst-case preview decompresses and parses
# up to 256MB of JSON, so this must not be free to spam.
@router.post("/pluralspace/preview", dependencies=[rate_limit(10, 60, "user")])
async def preview_pluralspace_import(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Open a PluralSpace export zip and return a counts summary."""
    data = await file.read()
    if len(data) > MAX_IMPORT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Import file too large. Max 100MB.",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Import file is empty.",
        )

    system = await _get_user_system(user, db)

    try:
        parsed = await parse_export_async(data)
    except ImportPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is not a valid zip archive.",
        ) from exc

    summary = preview(parsed)
    # Estimate: the import re-clamps the concurrent-open cap authoritatively.
    poll_warning = await open_poll_preview_warning(
        db, system.id, user, summary.open_poll_count
    )
    if poll_warning:
        summary.limit_warnings.append(poll_warning)
    retention_warning = front_retention_preview_warning(
        system.front_retention_days, summary.front_count > 0
    )
    if retention_warning:
        summary.limit_warnings.append(retention_warning)
    return summary.model_dump(mode="json")
