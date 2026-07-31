"""The anonymous public-profile surface.

This is the only router in the API that serves data to callers with no account.
Everything about it is built to fail closed:

- It is registered with NO `require_scope` dependency, and its handlers take no
  `get_current_user`. There is nothing to authenticate; there is also nothing an
  attacker can escalate.
- Exactly one gate: `resolve_public_grant` / `resolve_link_grant`, which return
  None for every failure mode (feature disabled, unknown system/token, no grant,
  grant pending, revoked, or expired). Every one becomes the IDENTICAL 404, so
  there is no oracle that distinguishes "never existed" from "went dark".
- Payloads come only from `share_projection`, which builds dedicated public
  schemas field-by-field; an ORM row is never serialised directly.
- `noindex` on every response, plus a short cache TTL so un-publishing actually
  propagates.

The whole router 404s wholesale when `PUBLIC_PROFILES_ENABLED` is off, so an
instance that never wants a public surface never has one.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.share import ShareView
from sheaf.models.system import System
from sheaf.schemas.public_profile import (
    PublicFrontingView,
    PublicMemberView,
    PublicSystemView,
)
from sheaf.services.share_projection import (
    project_fronting,
    project_members,
    project_system,
)
from sheaf.services.sharing import resolve_link_grant, resolve_public_grant

# Per-IP throttle: tight enough that scraping a system's whole profile is
# annoying, loose enough that a link pasted in a busy channel (many distinct
# IPs, each fetching the 2-3 endpoints of one page) is never hit. Anonymous, so
# there is no per-user key to use.
_RATE = rate_limit(60, 60)

router = APIRouter(prefix="/public", tags=["public-profiles"])


def _not_found() -> HTTPException:
    # One error shape for every reason a profile is not visible. Deliberately
    # says nothing about which reason.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _public_headers(response: Response) -> None:
    # Link-sharing, not search presence: never index a personal profile.
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    # Short TTL so un-publish propagates fast; long enough to absorb a link
    # pasted into a busy channel without hammering the origin.
    response.headers["Cache-Control"] = "public, max-age=60"


async def _resolve_system(
    system_id: uuid.UUID, db: AsyncSession
) -> tuple[ShareView, System]:
    if not settings.public_profiles_enabled:
        raise _not_found()
    resolved = await resolve_public_grant(db, system_id)
    if resolved is None:
        raise _not_found()
    _, view = resolved
    system = await db.get(System, system_id)
    if system is None:
        raise _not_found()
    return view, system


async def _resolve_link(token: str, db: AsyncSession) -> tuple[ShareView, System]:
    if not settings.public_profiles_enabled:
        raise _not_found()
    resolved = await resolve_link_grant(db, token)
    if resolved is None:
        raise _not_found()
    grant, view = resolved
    system = await db.get(System, grant.system_id)
    if system is None:
        raise _not_found()
    return view, system


# ---------------------------------------------------------------------------
# Public grant (located by the system's UUID)
# ---------------------------------------------------------------------------


@router.get(
    "/systems/{system_id}",
    response_model=PublicSystemView,
    dependencies=[_RATE],
)
async def public_system(
    system_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicSystemView:
    _public_headers(response)
    view, system = await _resolve_system(system_id, db)
    return await project_system(db, view, system)


@router.get(
    "/systems/{system_id}/members",
    response_model=list[PublicMemberView],
    dependencies=[_RATE],
)
async def public_system_members(
    system_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> list[PublicMemberView]:
    _public_headers(response)
    view, _ = await _resolve_system(system_id, db)
    return await project_members(db, view)


@router.get(
    "/systems/{system_id}/fronting",
    response_model=PublicFrontingView,
    dependencies=[_RATE],
)
async def public_system_fronting(
    system_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicFrontingView:
    _public_headers(response)
    view, system = await _resolve_system(system_id, db)
    # A view that does not include fronting 404s the fronting endpoint rather
    # than returning an empty body, so "is fronting shared?" is not probeable
    # separately from "is the profile public?".
    if not view.include_fronting:
        raise _not_found()
    return await project_fronting(db, view, system)


# ---------------------------------------------------------------------------
# Link grant (located by an opaque token)
# ---------------------------------------------------------------------------


@router.get(
    "/shared/{token}",
    response_model=PublicSystemView,
    dependencies=[_RATE],
)
async def public_shared(
    token: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicSystemView:
    _public_headers(response)
    view, system = await _resolve_link(token, db)
    return await project_system(db, view, system)


@router.get(
    "/shared/{token}/members",
    response_model=list[PublicMemberView],
    dependencies=[_RATE],
)
async def public_shared_members(
    token: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> list[PublicMemberView]:
    _public_headers(response)
    view, _ = await _resolve_link(token, db)
    return await project_members(db, view)


@router.get(
    "/shared/{token}/fronting",
    response_model=PublicFrontingView,
    dependencies=[_RATE],
)
async def public_shared_fronting(
    token: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicFrontingView:
    _public_headers(response)
    view, system = await _resolve_link(token, db)
    if not view.include_fronting:
        raise _not_found()
    return await project_fronting(db, view, system)
