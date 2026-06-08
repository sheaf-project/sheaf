"""PluralSpace import — preview endpoint.

The actual import runs through the async job runner (POST
/v1/imports/file with source=pluralspace_file). What's left here is
the synchronous preview: open the zip, count entities, return a
summary the user can confirm before enqueueing.
"""

from __future__ import annotations

import zipfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from sheaf.auth.dependencies import get_current_user
from sheaf.models.user import User
from sheaf.services.import_parsing import ImportPayloadError
from sheaf.services.pluralspace_import import parse_export, preview

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024


@router.post("/pluralspace/preview")
async def preview_pluralspace_import(
    file: UploadFile,
    _user: User = Depends(get_current_user),
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

    try:
        parsed = parse_export(data)
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
    return summary.model_dump(mode="json")
