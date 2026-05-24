"""Sheaf native re-import — preview endpoint.

The actual import runs asynchronously through the unified job runner
(POST /v1/imports/file). What's left here is the synchronous preview:
parse a Sheaf export and return a summary of importable data. Preview
writes nothing.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from sheaf.auth.dependencies import get_current_user
from sheaf.models.user import User
from sheaf.services.sheaf_import import preview

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
    # uploaded_files) over time; the importer now round-trips all of them
    # except image bytes (which the sync export omits anyway). A v1 file
    # simply lacks those keys and the importer skips them. New format
    # versions should be added here as they ship.
    if not isinstance(parsed, dict) or parsed.get("version") not in {"1", "2"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Not a valid Sheaf export file (missing or unsupported "
                "version field — expected 1 or 2)."
            ),
        )
    return parsed


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
        "journal_count": p.journal_count,
        "message_count": p.message_count,
        "poll_count": p.poll_count,
        "reminder_count": p.reminder_count,
        "channel_count": p.channel_count,
    }
