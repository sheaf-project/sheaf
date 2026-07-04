"""OpenPlural import - preview endpoint.

The actual import runs asynchronously through the unified job runner
(POST /v1/imports/file with source=openplural_file). This is the
synchronous preview: parse an OpenPlural v0.1 export, translate it to
the native shape, and summarise the importable data. Preview writes
nothing.

Accepts both shapes the exporter produces: a bare OpenPlural JSON
document and an `.openplural.zip` bundle (`openplural.json` +
`assets/`), distinguished by the zip magic bytes. The response carries
`archive` / `image_count` so the client knows which flavour it
previewed, and `lineage_length` so it can surface the file's prior
journey.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.services.import_parsing import ImportPayloadError
from sheaf.services.openplural_import import (
    inherited_lineage,
    looks_like_zip,
    parse_bundle_async,
    parse_json_async,
)
from sheaf.services.sheaf_import import (
    SheafPreviewSummary,
    open_poll_preview_warning,
    preview,
)

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024  # 100MB


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


@router.post("/openplural/preview")
async def preview_openplural_import(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Parse an OpenPlural export (JSON or .openplural.zip) and summarise it."""
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
        if looks_like_zip(data):
            parsed, envelope = await parse_bundle_async(data)
            summary = preview(parsed.data)
            # Estimate: the import re-clamps the concurrent-open cap
            # authoritatively.
            poll_warning = await open_poll_preview_warning(
                db, system.id, user, summary.open_poll_count
            )
            if poll_warning:
                summary.limit_warnings.append(poll_warning)
            return {
                **_summary_dict(summary),
                "archive": True,
                "image_count": len(parsed.image_keys),
                "lineage_length": len(inherited_lineage(envelope)),
            }
        native, envelope = await parse_json_async(data)
        summary = preview(native)
        # Estimate: the import re-clamps the concurrent-open cap authoritatively.
        poll_warning = await open_poll_preview_warning(
            db, system.id, user, summary.open_poll_count
        )
        if poll_warning:
            summary.limit_warnings.append(poll_warning)
        return {
            **_summary_dict(summary),
            "archive": False,
            "image_count": 0,
            "lineage_length": len(inherited_lineage(envelope)),
        }
    except ImportPayloadError as exc:
        # User-facing parse/version failures (bad JSON, bad zip, unknown
        # openplural_version) map to a 400 with the short message.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
