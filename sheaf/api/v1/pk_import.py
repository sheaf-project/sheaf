"""PluralKit data import endpoints.

Two ingestion paths share a single importer:

  - File: multipart upload of a PK data export JSON. Mirrors SP file import.
  - API: JSON body containing the user's PK token. We forward the token
    to PluralKit on a single round of requests, never log it, and never
    persist it.

Both paths use the same options/preview/result schemas because they
produce the same canonical PK-shaped dict that the importer consumes.
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
from sheaf.schemas.pk_import import (
    PKApiImportRequest,
    PKApiPreviewRequest,
    PKImportOptions,
    PKImportResult,
    PKPreviewSummary,
)
from sheaf.services.pk_api import (
    PKApiError,
    fetch_export,
    fetch_switch_sample,
)
from sheaf.services.pk_import import preview as build_preview
from sheaf.services.pk_import import run_import

logger = logging.getLogger("sheaf.import.pk")

router = APIRouter(prefix="/import", tags=["import"])

MAX_IMPORT_SIZE = 100 * 1024 * 1024  # 100MB — PK exports are tiny but be safe


async def _parse_upload(file: UploadFile) -> dict[str, Any]:
    """Read and parse a PK export JSON file from a multipart upload."""
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
            detail="Export file does not contain a PluralKit system object.",
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
    system_profile: bool,
    member_ids: str | None,
    groups: bool,
    front_history: bool,
) -> PKImportOptions:
    parsed_member_ids: list[str] | None = None
    if member_ids is not None:
        parsed_member_ids = [m.strip() for m in member_ids.split(",") if m.strip()]
    return PKImportOptions(
        system_profile=system_profile,
        member_ids=parsed_member_ids,
        groups=groups,
        front_history=front_history,
    )


def _api_error_to_http(exc: PKApiError) -> HTTPException:
    if exc.status_code in (401, 403):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    if exc.status_code == 404:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if exc.status_code == 429:
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


# --- File path ---------------------------------------------------------------


@router.post("/pluralkit/preview", response_model=PKPreviewSummary)
async def preview_file_import(
    file: UploadFile,
    _user: User = Depends(get_current_user),
):
    """Parse a PK export file and return a summary."""
    data = await _parse_upload(file)
    return build_preview(data)


@router.post("/pluralkit", response_model=PKImportResult)
async def do_file_import(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    system_profile: bool = True,
    member_ids: str | None = None,
    groups: bool = True,
    front_history: bool = False,
):
    """Import data from a PluralKit export file.

    Use the preview endpoint first to see what's in the file and to gather
    HIDs for the optional member_ids parameter (comma-separated).
    """
    data = await _parse_upload(file)
    system = await _get_system(user, db)
    options = _options_from_query(system_profile, member_ids, groups, front_history)
    return await run_import(data, options, system, db)


# --- API path ----------------------------------------------------------------


@router.post("/pluralkit-api/preview", response_model=PKPreviewSummary)
async def preview_api_import(
    body: PKApiPreviewRequest,
    _user: User = Depends(get_current_user),
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
        raise _api_error_to_http(exc) from exc

    data["switches"] = switch_sample
    # Surface "100+" via override so the user knows there are more switches
    # than the preview page sampled.
    override = len(switch_sample) if not has_more else None
    summary = build_preview(data, switch_count_override=override)
    if has_more:
        # Set the count to the page size; UI will format "100+".
        summary.switch_count = len(switch_sample)
    return summary


@router.post("/pluralkit-api", response_model=PKImportResult)
async def do_api_import(
    body: PKApiImportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import a system via the PluralKit API.

    The token is forwarded for the duration of this request and then
    discarded. Nothing about the token is persisted to the database.
    """
    try:
        data = await fetch_export(body.token, include_switches=body.options.front_history)
    except PKApiError as exc:
        raise _api_error_to_http(exc) from exc

    system = await _get_system(user, db)
    return await run_import(data, body.options, system, db)
