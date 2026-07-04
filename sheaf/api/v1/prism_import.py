"""Prism (.prism) import — preview endpoint.

The full import runs through the unified async job runner; this
endpoint decrypts a PRISM1 envelope with a user-supplied passphrase
and returns entity counts so the user can confirm before enqueueing.
The passphrase is consumed in-memory only — nothing is persisted by
the preview path.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.services.import_parsing import ImportPayloadError
from sheaf.services.prism_import import parse_envelope_bytes_async, preview
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


# Tight per-user rate limit: each preview runs a full scrypt KDF
# (bounded but expensive), so this must not be free to spam.
@router.post("/prism/preview", dependencies=[rate_limit(5, 60, "user")])
async def preview_prism_import(
    file: Annotated[UploadFile, File()],
    passphrase: Annotated[str, Form()],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Decrypt a PRISM1 envelope and return a counts summary.

    Failures (wrong passphrase, malformed envelope, truncated file)
    surface as 400 with the parser's user-facing message.
    """
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
    if not passphrase:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decryption passphrase is required.",
        )

    system = await _get_user_system(user, db)

    try:
        parsed = await parse_envelope_bytes_async(data, passphrase)
    except ImportPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    summary = preview(parsed)
    # Estimate: the import re-clamps the concurrent-open cap authoritatively.
    poll_warning = await open_poll_preview_warning(
        db, system.id, user, summary.open_poll_count
    )
    if poll_warning:
        summary.limit_warnings.append(poll_warning)
    return summary.model_dump(mode="json")
