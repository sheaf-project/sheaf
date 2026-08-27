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
  propagates. Only the success path is set here; `PublicShareHeadersMiddleware`
  fills the same headers in on the 404s, 429s, and anything else built by an
  exception handler that never sees this router's `Response`.

The whole router 404s wholesale when `PUBLIC_PROFILES_ENABLED` is off, so an
instance that never wants a public surface never has one.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.share import ShareView
from sheaf.models.system import System
from sheaf.schemas.public_profile import (
    PublicFrontingView,
    PublicGroupsView,
    PublicMemberView,
    PublicRelationshipsView,
    PublicSystemView,
)
from sheaf.services.share_projection import (
    project_fronting,
    project_groups,
    project_members,
    project_relationships,
    project_system,
)
from sheaf.services.sharing import resolve_link_grant, resolve_public_grant

# One shared per-IP throttle across every public-profile route. A fixed bucket
# means varying system ids or bearer tokens cannot create fresh quotas, and raw
# link capabilities never become part of Redis key names. Sixty requests still
# permits a dozen complete page loads per minute from one IP, with the page now
# spanning up to five endpoints (system, members, fronting, relationships,
# groups). Member permalinks share the bucket deliberately: they are the one
# route whose path a visitor can vary freely, so they must not have a quota of
# their own to walk a system's member ids through.
#
# fail_closed: the public projection is the most expensive anonymous query on
# the instance, so a Redis blip must not drop the one throttle that bounds it.
# Unlike an ordinary endpoint, availability-through-the-blip is the wrong
# trade here; we 503 until the counter is back, matching the auth endpoints.
_RATE = rate_limit(60, 60, fail_closed=True, bucket="public_profiles")


def _not_found() -> HTTPException:
    # One error shape for every reason a profile is not visible. Deliberately
    # says nothing about which reason.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _reject_query_params(request: Request) -> None:
    """Reject any query string: none of these routes take a query parameter.

    FastAPI would silently ignore an unknown param, but a shared cache keys on
    the whole URL, so ?cachebust=N is a guaranteed miss that re-runs the most
    expensive anonymous query on the instance once per varied value - a cheap
    cache-buster aimed at exactly the endpoints that can least afford it. The
    media route holds the same line with its canonical-URL check. The refusal
    is the SAME 404 as every other one here, so it never becomes an oracle.
    """
    if request.scope.get("query_string"):
        raise _not_found()


# The query-param rejection is a router-level dependency so it covers every
# projection route here, including any added later, without a per-route opt-in.
router = APIRouter(
    prefix="/public",
    tags=["public-profiles"],
    dependencies=[Depends(_reject_query_params)],
)


def _public_headers(response: Response, *, token_keyed: bool) -> None:
    # Link-sharing, not search presence: never index a personal profile.
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    # Short TTL either way, so un-publishing propagates fast while a link
    # pasted into a busy channel does not hammer the origin. A token-keyed URL
    # carries a bearer capability in its path, so only the requesting client
    # may store it: a shared cache holding it would keep serving the profile
    # from an intermediary after the token is rotated or revoked, and would
    # park the token itself in someone else's storage. The /systems/ URLs
    # contain no secret and stay shared-cacheable.
    response.headers["Cache-Control"] = (
        "private, max-age=60" if token_keyed else "public, max-age=60"
    )


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


def _require_roster(view: ShareView) -> None:
    """404 the member surfaces when the view does not serve its roster.

    Same no-oracle rule as fronting and relationships, and worth spelling out:
    turning `include_members` off makes the roster UNADDRESSABLE, not empty.
    An empty list would answer "does this profile have members?" for anyone
    who asked, which is a fact about the system and not one the owner chose to
    publish. The curation itself is untouched and comes straight back when the
    flag does.
    """
    if not view.include_members:
        raise _not_found()


async def _member_permalink(
    db: AsyncSession, view: ShareView, system: System, member_id: uuid.UUID
) -> PublicMemberView:
    """The single-member payload, built once for both grant types.

    It goes back through `project_members` filtered to one id rather than
    fetching the member itself: a permalink must serve exactly the card the
    list would have served, and the only way to guarantee that is for there to
    be one place that builds it. Every guard the list obeys - the view's
    allowlist, the ACTIVE status, `never_shareable`, the member privacy
    ceiling, the bio flag, the exposed field set - therefore applies here
    without being restated, which is the point.
    """
    if not view.member_permalinks:
        raise _not_found()
    _require_roster(view)
    cards = await project_members(
        db, view, owner_id=system.user_id, only_id=member_id
    )
    if not cards:
        raise _not_found()
    return cards[0]


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
    _public_headers(response, token_keyed=False)
    view, system = await _resolve_system(system_id, db)
    # The visitor reached this by typing the system id, so echoing it back
    # discloses nothing they did not already have.
    return await project_system(db, view, system, expose_system_id=True)


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
    _public_headers(response, token_keyed=False)
    view, system = await _resolve_system(system_id, db)
    _require_roster(view)
    return await project_members(db, view, owner_id=system.user_id)


@router.get(
    "/systems/{system_id}/members/{member_id}",
    response_model=PublicMemberView,
    dependencies=[_RATE],
)
async def public_system_member(
    system_id: uuid.UUID,
    member_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicMemberView:
    """One member, at a stable address of their own.

    Three ways to get the same 404, and they are indistinguishable on purpose:
    the view does not hand out permalinks, the view does not serve its roster
    at all, or that member is not one this view projects. A visitor who tries a
    member id from somewhere else learns nothing about whether it exists, is in
    this system, or was quietly removed.
    """
    _public_headers(response, token_keyed=False)
    view, system = await _resolve_system(system_id, db)
    return await _member_permalink(db, view, system, member_id)


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
    _public_headers(response, token_keyed=False)
    view, system = await _resolve_system(system_id, db)
    # A view that does not include fronting 404s the fronting endpoint rather
    # than returning an empty body, so "is fronting shared?" is not probeable
    # separately from "is the profile public?".
    if not view.include_fronting:
        raise _not_found()
    return await project_fronting(db, view, system)


@router.get(
    "/systems/{system_id}/relationships",
    response_model=PublicRelationshipsView,
    dependencies=[_RATE],
)
async def public_system_relationships(
    system_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicRelationshipsView:
    _public_headers(response, token_keyed=False)
    view, _ = await _resolve_system(system_id, db)
    # Same no-oracle rule as fronting: a view without relationships 404s rather
    # than returning an empty list, so "does this profile share relationships?"
    # cannot be answered separately from "is this profile public?".
    if not view.include_relationships:
        raise _not_found()
    return await project_relationships(db, view)


@router.get(
    "/systems/{system_id}/groups",
    response_model=PublicGroupsView,
    dependencies=[_RATE],
)
async def public_system_groups(
    system_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicGroupsView:
    _public_headers(response, token_keyed=False)
    view, system = await _resolve_system(system_id, db)
    # Same no-oracle rule as fronting and relationships: a view without groups
    # 404s rather than returning an empty list, so "does this profile show
    # groups?" cannot be answered separately from "is this profile public?".
    if not view.include_groups:
        raise _not_found()
    return await project_groups(db, view, owner_id=system.user_id)


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
    _public_headers(response, token_keyed=True)
    view, system = await _resolve_link(token, db)
    # NOT the system id. An unlisted link is an opaque token so that the system
    # behind it cannot be named; putting the id in the body would have handed
    # every link holder the one identifier that ties their link to the owner's
    # public profile, and to every other link on the same system.
    return await project_system(db, view, system, expose_system_id=False)


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
    _public_headers(response, token_keyed=True)
    view, system = await _resolve_link(token, db)
    _require_roster(view)
    return await project_members(db, view, owner_id=system.user_id)


@router.get(
    "/shared/{token}/members/{member_id}",
    response_model=PublicMemberView,
    dependencies=[_RATE],
)
async def public_shared_member(
    token: str,
    member_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicMemberView:
    _public_headers(response, token_keyed=True)
    view, system = await _resolve_link(token, db)
    return await _member_permalink(db, view, system, member_id)


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
    _public_headers(response, token_keyed=True)
    view, system = await _resolve_link(token, db)
    if not view.include_fronting:
        raise _not_found()
    return await project_fronting(db, view, system)


@router.get(
    "/shared/{token}/relationships",
    response_model=PublicRelationshipsView,
    dependencies=[_RATE],
)
async def public_shared_relationships(
    token: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicRelationshipsView:
    _public_headers(response, token_keyed=True)
    view, _ = await _resolve_link(token, db)
    if not view.include_relationships:
        raise _not_found()
    return await project_relationships(db, view)


@router.get(
    "/shared/{token}/groups",
    response_model=PublicGroupsView,
    dependencies=[_RATE],
)
async def public_shared_groups(
    token: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicGroupsView:
    _public_headers(response, token_keyed=True)
    view, system = await _resolve_link(token, db)
    if not view.include_groups:
        raise _not_found()
    return await project_groups(db, view, owner_id=system.user_id)
