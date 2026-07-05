"""Sheaf native re-import - preview endpoint.

The actual import runs asynchronously through the unified job runner
(POST /v1/imports/file). What's left here is the synchronous preview:
parse a Sheaf export and return a summary of importable data. Preview
writes nothing.

Accepts both shapes the exporter produces: the plain `/v1/export` JSON
file and the export-with-images zip (`export.json` + `images/`),
distinguished by the zip magic bytes. The response carries `archive` /
`image_count` so the client knows which flavour it previewed and which
source value to submit the job under.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.services.front_retention import front_retention_preview_warning
from sheaf.services.import_parsing import ImportPayloadError, safe_json_loads_async
from sheaf.services.sheaf_archive_import import parse_archive_async
from sheaf.services.sheaf_archive_import import preview as archive_preview
from sheaf.services.sheaf_import import (
    SheafPreviewSummary,
    open_poll_preview_warning,
    preview,
)

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024  # 100MB

_ZIP_MAGIC = b"PK"


async def _parse_json_export(data: bytes) -> dict:
    """Parse + shape-check a plain Sheaf export JSON file."""
    try:
        # Element-count cap (json-bomb guard), same as the async runner's
        # parse path - a preview must not be cheaper to DoS than the
        # import itself. Parsed off the event loop so a large export does
        # not stall the loop while it decodes.
        parsed = await safe_json_loads_async(data)
    except ImportPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON file.",
        ) from exc

    # Accept both legacy v1 exports and current v2 exports. v2 added new
    # top-level keys (reminders, watch_tokens, polls, journals, revisions,
    # uploaded_files) over time; the importer round-trips all of them. A
    # v1 file simply lacks those keys and the importer skips them. New
    # format versions should be added here as they ship.
    if not isinstance(parsed, dict) or parsed.get("version") not in {"1", "2"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Not a valid Sheaf export file (missing or unsupported "
                "version field - expected 1 or 2)."
            ),
        )
    return parsed


def _summary_dict(p: SheafPreviewSummary) -> dict:
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
        "open_poll_count": p.open_poll_count,
        "reminder_count": p.reminder_count,
        "channel_count": p.channel_count,
        "limit_warnings": p.limit_warnings,
    }


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


@router.post("/sheaf/preview")
async def preview_import(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Parse a Sheaf export (JSON or with-images zip) and summarise it."""
    data = await file.read()
    if len(data) > MAX_IMPORT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Import file too large. Max 100MB.",
        )

    system = await _get_user_system(user, db)

    if data[:2] == _ZIP_MAGIC:
        try:
            parsed = await parse_archive_async(data)
        except ImportPayloadError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        summary, image_count = archive_preview(parsed)
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
        return {
            **_summary_dict(summary),
            "archive": True,
            "image_count": image_count,
        }

    parsed_json = await _parse_json_export(data)
    summary = preview(parsed_json)
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
    return {
        **_summary_dict(summary),
        "archive": False,
        "image_count": 0,
    }
