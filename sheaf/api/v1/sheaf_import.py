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

from sheaf.auth.dependencies import get_current_user
from sheaf.models.user import User
from sheaf.services.import_parsing import ImportPayloadError, safe_json_loads
from sheaf.services.sheaf_archive_import import parse_archive_async
from sheaf.services.sheaf_archive_import import preview as archive_preview
from sheaf.services.sheaf_import import SheafPreviewSummary, preview

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024  # 100MB

_ZIP_MAGIC = b"PK"


def _parse_json_export(data: bytes) -> dict:
    """Parse + shape-check a plain Sheaf export JSON file."""
    try:
        # Element-count cap (json-bomb guard), same as the async runner's
        # parse path - a preview must not be cheaper to DoS than the
        # import itself.
        parsed = safe_json_loads(data)
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
        "reminder_count": p.reminder_count,
        "channel_count": p.channel_count,
    }


@router.post("/sheaf/preview")
async def preview_import(
    file: UploadFile,
    _user: User = Depends(get_current_user),
):
    """Parse a Sheaf export (JSON or with-images zip) and summarise it."""
    data = await file.read()
    if len(data) > MAX_IMPORT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Import file too large. Max 100MB.",
        )

    if data[:2] == _ZIP_MAGIC:
        try:
            parsed = await parse_archive_async(data)
        except ImportPayloadError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        summary, image_count = archive_preview(parsed)
        return {
            **_summary_dict(summary),
            "archive": True,
            "image_count": image_count,
        }

    parsed_json = _parse_json_export(data)
    return {
        **_summary_dict(preview(parsed_json)),
        "archive": False,
        "image_count": 0,
    }
