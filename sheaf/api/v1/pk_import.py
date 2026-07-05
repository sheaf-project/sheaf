"""PluralKit import — preview endpoints.

The actual import runs asynchronously through the unified job runner
(POST /v1/imports/file or /v1/imports/api). What's left here is the
synchronous *preview* step: parse an export (file or live API) and
return a summary so the user can review + deselect members before
enqueueing the real import. Preview does not write anything.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.pk_import import (
    PKApiPreviewRequest,
    PKPreviewSummary,
)
from sheaf.services.front_retention import front_retention_preview_warning
from sheaf.services.import_parsing import ImportPayloadError, safe_json_loads_async
from sheaf.services.pk_api import (
    PKApiError,
    fetch_export,
    fetch_switch_sample,
)
from sheaf.services.pk_import import preview as build_preview

logger = logging.getLogger("sheaf.import.pk")

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024  # 100MB — PK exports are tiny but be safe


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


async def _parse_upload(file: UploadFile) -> dict[str, Any]:
    """Read and parse a PK export JSON file from a multipart upload."""
    data = await file.read()
    if len(data) > MAX_IMPORT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Import file too large. Max 100MB.",
        )
    try:
        parsed = await safe_json_loads_async(data)
    except ImportPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON file.",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Export file does not contain a PluralKit system object.",
        )
    return parsed


def _api_error_to_http(exc: PKApiError) -> HTTPException:
    if exc.status_code in (401, 403):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    if exc.status_code == 404:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if exc.status_code == 429:
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        )
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


# --- File path ---------------------------------------------------------------


@router.post("/pluralkit/preview", response_model=PKPreviewSummary)
async def preview_file_import(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Parse a PK export file and return a summary."""
    data = await _parse_upload(file)
    summary = build_preview(data)
    system = await _get_user_system(user, db)
    retention_warning = front_retention_preview_warning(
        system.front_retention_days, summary.switch_count > 0
    )
    if retention_warning:
        summary.limit_warnings.append(retention_warning)
    return summary


# --- API path ----------------------------------------------------------------


@router.post("/pluralkit-api/preview", response_model=PKPreviewSummary)
async def preview_api_import(
    body: PKApiPreviewRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview an import by reading from the PluralKit API directly.

    The token is request-scoped: we use it for a single round-trip to
    fetch the system + members + groups + a single page of switches, and
    drop it on response. Nothing is logged, nothing persisted.
    """
    try:
        # Skip the full-history pull during preview — only one switch page.
        data = await fetch_export(body.token, include_switches=False)
        switch_sample, has_more = await fetch_switch_sample(body.token)
    except PKApiError as exc:
        # Log so a PK outage / rate-limiting (as opposed to a user's bad
        # token) is visible to operators. The token itself is never
        # logged — only the status code (when it's an HTTP error) and
        # the message.
        where = (
            f"HTTP {exc.status_code}"
            if exc.status_code is not None
            else "connection error"
        )
        logger.warning("PK API preview failed (%s): %s", where, exc)
        raise _api_error_to_http(exc) from exc

    data["switches"] = switch_sample
    # Surface "100+" via override so the user knows there are more switches
    # than the preview page sampled.
    override = len(switch_sample) if not has_more else None
    summary = build_preview(data, switch_count_override=override)
    if has_more:
        # Set the count to the page size; UI will format "100+".
        summary.switch_count = len(switch_sample)
    system = await _get_user_system(user, db)
    retention_warning = front_retention_preview_warning(
        system.front_retention_days, summary.switch_count > 0
    )
    if retention_warning:
        summary.limit_warnings.append(retention_warning)
    return summary
