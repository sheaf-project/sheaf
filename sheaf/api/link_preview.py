"""The crawler-facing HTML for `/p/` and `/s/` URLs, and the images those cards use.

This is the only server-rendered HTML in Sheaf, and it exists because a link
unfurler does not run JavaScript. The single-page app can put whatever it likes
in the DOM after it boots; the crawler already left with whatever was in the
static shell, which is one generic card for every URL on the instance. So the
per-URL card has to come from the server, and this is it.

Two independent per-view settings decide what a card says, and they are separate
columns rather than two rungs of one dial because neither exposure contains the
other:

- `ShareView.link_preview_mode` - the SYSTEM card, on `/p/{system_id}`. Its name,
  avatar and a snippet of its description.
- `ShareView.member_link_preview_mode` - a MEMBER PERMALINK card, on
  `/p/{system_id}/member/{member_id}`. That member's name and avatar, and
  deliberately nothing else.

Deployment note, because this router is useless without it: these routes sit at
the app ROOT, on the same paths the SPA serves to people, and the reverse proxy
decides which of the two answers a given request. The shipped configs route
`/p/*` and `/s/*` to the backend ONLY when the User-Agent is a known unfurler,
and to the static app otherwise. That split is deliberate:

- Real visitors never touch this code, so there is no server-rendered copy of the
  app to drift from the client-rendered one, and no way for this router to break
  the actual page.
- A missed User-Agent degrades to the generic static card, which is the safe
  answer. Failing closed is the whole reason the matching lives in the proxy
  rather than being trusted to be complete.
- If the backend is down, profiles still load for people.

Every route here also has a `/v1/link-preview/...` alias, on the same handler,
for the same reason `security.txt` is registered twice: the aliased path is
reachable through every existing proxy config with no changes, so an operator can
verify what their instance will unfurl (`curl` it) before touching any routing,
and the tests do not have to assume a proxy exists. The alias serves
byte-identical responses under byte-identical rules; it is a second address, not a
second policy.

No DOCUMENT here can be an oracle. Every document request gets `200` and a card -
an unknown system, a revoked link, a malformed UUID, a system that was never
public and a system whose publish is still inside its grace window all produce the
SAME generic card. That is the opposite of the JSON surface's uniform `404`, and
for the same reason: a crawler that got "no preview" for one URL and "a preview"
for another would have been told which links are real.

The IMAGE routes are the exception, and answer a uniform `404` instead, because
they are not an oracle either way round: a card only ever references its image URL
when it is already a rich card, so "this URL serves an image" tells a prober
exactly what fetching the document would have told them anyway.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.share import LinkPreviewMode, ShareView
from sheaf.models.system import System
from sheaf.observability.metrics import link_previews_total
from sheaf.observability.unfurler import unfurler_from
from sheaf.schemas.public_profile import PublicMemberView, PublicSystemView
from sheaf.services.link_preview import (
    LinkPreview,
    absolute_url,
    build_link_preview,
    build_member_preview,
    generic_image_url,
    generic_preview,
    render_preview_html,
)
from sheaf.services.share_projection import project_members, project_system
from sheaf.services.sharing import resolve_public_grant

router = APIRouter(include_in_schema=False)

# Its own bucket, and a looser one than the JSON surface's 60/min. A single paste
# into a busy channel can fan out to a dozen unfurlers at once (Discord, the
# poster's own client, every bridge in the room), and throttling that to the same
# budget as one person browsing would drop cards for no gain. Still bounded,
# still per-IP, and still `fail_closed` for the reason the JSON routes are: this
# runs the projection query, so the throttle in front of it is not optional.
#
# `bucket` is fixed rather than derived from the path because `/s/{token}` puts a
# bearer capability in the URL and a derived key would copy it into Redis.
_RATE = rate_limit(120, 60, fail_closed=True, bucket="link_preview")

# The image routes get their own budget. One unfurl is one document fetch plus one
# image fetch, often from different hosts inside the same service, so sharing a
# bucket would mean a busy channel spent its document allowance on pictures.
_IMAGE_RATE = rate_limit(120, 60, fail_closed=True, bucket="link_preview_image")

# How long a crawler may reuse a card or an image before asking again. Deliberately
# the same 60 seconds the anonymous JSON surface uses: the point of these routes is
# that unpublishing takes effect, and a long max-age would hand a cache the right
# to keep serving a profile the owner has taken down. Unfurlers normally re-host
# the image bytes on their own CDN anyway, so a short origin TTL costs nothing.
_MAX_AGE = 60


def _headers(*, token_keyed: bool) -> dict[str, str]:
    """The response headers, which are half the safety of this surface.

    `X-Robots-Tag` mirrors the JSON routes and the proxy: unfurling a link
    somebody deliberately sent is the point, being indexed never was, and this
    document is the one place on the public surface that WANTS to be read by a
    robot - so saying "not that kind of robot" explicitly matters more here than
    anywhere else.

    The CSP is `default-src 'none'` because this document genuinely needs nothing:
    no scripts, no styles, no fonts, no frames. `img-src 'self'` is there only so
    a crawler that renders the page can fetch the avatar it was just handed, and
    that avatar is always same-origin (the image routes below are on this origin).

    `Cache-Control` splits on whether the URL itself is a secret, exactly as
    `public_profiles._public_headers` does. A `/s/` URL carries a bearer token in
    its path, so only the requesting client may store the response; a shared cache
    holding it would park the token in somebody else's storage and keep answering
    after a rotate.
    """
    return {
        "X-Robots-Tag": "noindex, nofollow",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'none'; img-src 'self'; base-uri 'none'; form-action 'none'"
        ),
        "Cache-Control": (
            f"private, max-age={_MAX_AGE}"
            if token_keyed
            else f"public, max-age={_MAX_AGE}"
        ),
    }


def _origin(request: Request) -> str:
    """The absolute origin to build `og:image` and `og:url` against.

    `SHEAF_BASE_URL` first: it is the operator's declared public address, it is
    already the trusted source for the same job in verification emails and
    `security.txt`, and it cannot be moved by a request header. The request's own
    base URL is the fallback for instances that have not set it (it is only
    mandatory when email is on).

    Falling back to the request means a spoofed `Host` can appear in a card's
    `og:image`/`og:url`. The residual is small and bounded: the card is served to
    whoever asked, so a spoofed host only mis-addresses that requester's own card,
    and every field on it is already public either way. It is still a reason to
    set `SHEAF_BASE_URL`, and the selfhosting docs say so.
    """
    if settings.sheaf_base_url:
        return settings.sheaf_base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


def _count_card(request: Request, card: str) -> None:
    """One increment per document served: which card, and which service asked.

    The UA is folded into a bounded set before it goes anywhere near a label
    (`unfurler_from`), so a crawler cannot mint series by lying about itself.
    Documents only: the image a rich card points at is fetched as a
    consequence of the card and would double-count the paste.
    """
    link_previews_total.labels(
        card=card, unfurler=unfurler_from(request.headers.get("user-agent"))
    ).inc()


def _document(preview: LinkPreview, *, token_keyed: bool) -> Response:
    return Response(
        content=render_preview_html(preview),
        media_type="text/html; charset=utf-8",
        headers=_headers(token_keyed=token_keyed),
    )


def _logo() -> str | None:
    """The instance logo for a generic card, or None if there is no base URL.

    Deliberately read from the SETTING rather than from the request, unlike
    `_origin`. The generic card has to be identical for `/p/` and `/s/` alike,
    and the `/s/` handler takes no request on purpose - it takes nothing at
    all, so that there is no path from a share link to anything about the
    system behind it. Deriving the logo per request would have meant giving it
    one.
    """
    return generic_image_url(settings.sheaf_base_url)


def _generic(page_url: str | None, *, token_keyed: bool) -> Response:
    """The generic card, carrying the instance logo.

    The logo is the same bytes for every URL on the instance, one that resolves
    and one that does not alike, so it does not make the card an oracle. It
    just stops a link unfurling as a bare line of text, which some clients
    render as barely a link at all.
    """
    return _document(
        generic_preview(page_url, image_url=_logo()),
        token_keyed=token_keyed,
    )


def _no_image() -> HTTPException:
    """One refusal for every reason an image is not served.

    Same shape and same body whatever the reason, matching the anonymous JSON
    surface's single 404: not published, mode not raised, still inside a grace
    window, member not visible, no avatar set, suppressed account.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _image_redirect(url: str) -> RedirectResponse:
    """Send the crawler at the avatar the page would have shown it.

    A 302 rather than streaming the bytes, because the projection has already done
    the work of turning the stored reference into a URL this instance is willing to
    serve - including signing it, and including whatever the instance's external
    image policy decided - and re-implementing that here would mean a second
    opinion about which bytes are allowed out. The existing `/v1/files/{key}` route
    already 302s to presigned storage URLs in S3 mode, so redirect-following is
    table stakes for anything fetching images from a Sheaf instance.

    The redirect target itself is short-lived and re-checks that the account is
    still serving publicly on every fetch (`account_serving_public_media`), which
    is what makes this safe to hand out: the capability cannot outlive the profile
    even though the address in the card does.
    """
    return RedirectResponse(
        url=url,
        status_code=status.HTTP_302_FOUND,
        headers={
            "Cache-Control": f"public, max-age={_MAX_AGE}",
            "X-Robots-Tag": "noindex, nofollow",
            "Referrer-Policy": "no-referrer",
        },
    )


# ---------------------------------------------------------------------------
# The single gate in front of everything rich
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RichSystem:
    """A `/p/{system_id}` URL that has cleared every gate for a rich card."""

    view: ShareView
    system: System
    projection: PublicSystemView


async def _resolve_rich_system(
    db: AsyncSession, raw_system_id: str
) -> _RichSystem | None:
    """The gate for a rich SYSTEM card, or None.

    Both the system document and the system image go through this, which is the
    point: the image must not be able to serve for a URL whose card would not, and
    the only way to guarantee that is for there to be one function that decides. A
    parallel copy of these conditions in the image route is exactly the drift this
    avoids.

    Order of decisions, all of which have to say yes:

    1. The instance publishes at all (`public_profiles_enabled`). With the surface
       off the page is a 404 for everybody.
    2. The path is a well-formed UUID. A malformed one cannot be a system, and it
       short-circuits before the database so a cache-buster costs nothing.
    3. `resolve_public_grant` returns a grant. This single call enforces three
       separate staging rules without this function mentioning any of them: a grant
       still inside its grace window is excluded (`include_pending=False`), a
       system whose raise to public is parked in `pending_privacy` fails
       `profile_serving_clause`, and a suspended, banned, pending-deletion or
       operator-blocked account fails it too. So "staged publishing previews as
       generic" is a property of the resolver rather than a check that could be
       forgotten here.
    4. The view's LIVE `link_preview_mode` is `system_details`. The live column,
       never `pending_link_preview_mode`: a staged raise has not happened yet, and a
       grace period that held back the profile while leaking its name into a chat
       would have defeated itself.

    The projection is built with `project_system` - the same call
    `GET /v1/public/systems/{id}` makes, with the same view, on the same resolved
    grant - so no field can reach a card that the URL does not already serve to
    anyone who asks.
    """
    base = await _resolve_public_view(db, raw_system_id)
    if base is None:
        return None
    view, system = base
    if view.link_preview_mode != LinkPreviewMode.SYSTEM_DETAILS.value:
        return None
    projection = await project_system(db, view, system, expose_system_id=True)
    return _RichSystem(view=view, system=system, projection=projection)


@dataclass(frozen=True)
class _RichMember:
    """A member permalink URL that has cleared every gate for a rich card."""

    view: ShareView
    system: System
    card: PublicMemberView


async def _resolve_rich_member(
    db: AsyncSession, raw_system_id: str, raw_member_id: str
) -> _RichMember | None:
    """The gate for a rich MEMBER card, or None.

    Everything `_resolve_rich_system` requires about the system and the grant, plus
    three conditions that belong to member permalinks specifically:

    1. `view.member_permalinks` is on. With it off the permalink URL is a 404, so a
       card for it would be advertising a page that does not exist.
    2. The view's LIVE `member_link_preview_mode` is `system_details`. Its own
       column, independent of the system card's: a member card reveals one member's
       name and avatar, which is neither a subset nor a superset of the system's
       name, avatar and description, so neither setting implies the other.
    3. **The member is actually one this view serves.** Decided by asking
       `project_members` with `only_id`, which is the same call the real permalink
       route makes - so the view's allowlist, the ACTIVE status, `never_shareable`,
       the member's own privacy ceiling, archived and pending-deletion members are
       all applied without being restated. A private member sitting inside an
       otherwise public view previews generic, and it does so because the projection
       said so rather than because this function remembered to ask.

    That third point is the one worth being strict about. Re-deriving "is this
    member visible" here would put a second copy of the visibility rule in the
    least-examined part of the codebase, and the two would drift.
    """
    base = await _resolve_public_view(db, raw_system_id)
    if base is None:
        return None
    view, system = base
    if not view.member_permalinks:
        return None
    if view.member_link_preview_mode != LinkPreviewMode.SYSTEM_DETAILS.value:
        return None
    try:
        member_id = uuid.UUID(raw_member_id)
    except (ValueError, AttributeError, TypeError):
        return None

    cards = await project_members(
        db, view, owner_id=system.user_id, only_id=member_id
    )
    if not cards:
        return None
    return _RichMember(view=view, system=system, card=cards[0])


async def _resolve_public_view(
    db: AsyncSession, raw_system_id: str
) -> tuple[ShareView, System] | None:
    """Steps 1 to 3 of both gates above: is this `/p/` URL serving anything now.

    Split out so the system and member gates cannot disagree about the part that is
    not about which card is being built.
    """
    if not settings.public_profiles_enabled:
        return None
    try:
        system_id = uuid.UUID(raw_system_id)
    except (ValueError, AttributeError, TypeError):
        return None
    resolved = await resolve_public_grant(db, system_id)
    if resolved is None:
        return None
    _, view = resolved
    system = await db.get(System, system_id)
    if system is None:
        return None
    return view, system


# ---------------------------------------------------------------------------
# System profile: /p/{system_id}
# ---------------------------------------------------------------------------


@router.get("/p/{raw_system_id}", dependencies=[_RATE])
@router.get("/v1/link-preview/p/{raw_system_id}", dependencies=[_RATE])
async def system_link_preview(
    raw_system_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The system card, or the generic one. Always 200.

    `og:image` points at this URL's own `/preview-image` child rather than at the
    signed avatar URL directly, and only when there is an avatar to serve - see
    `system_preview_image` for why that indirection is the whole point.
    """
    origin = _origin(request)
    page_url = f"{origin}/p/{raw_system_id}"

    rich = await _resolve_rich_system(db, raw_system_id)
    if rich is None:
        _count_card(request, "generic")
        return _generic(page_url, token_keyed=False)

    _count_card(request, "system_details")
    image_url = (
        f"{page_url}/preview-image" if rich.projection.avatar_url else None
    )
    return _document(
        build_link_preview(
            rich.projection,
            rich=True,
            page_url=page_url,
            image_url=image_url,
            logo_url=_logo(),
        ),
        token_keyed=False,
    )


@router.get("/p/{raw_system_id}/preview-image", dependencies=[_IMAGE_RATE])
@router.get(
    "/v1/link-preview/p/{raw_system_id}/preview-image", dependencies=[_IMAGE_RATE]
)
async def system_preview_image(
    raw_system_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The system avatar, resolved fresh on every request.

    This route exists so that the card's `og:image` can be a STABLE address. The
    obvious implementation - put the projection's signed avatar URL straight in the
    tag - rots: that URL carries a capability good for an hour or two, so an
    unfurler that cached the address and refetched the picture later got a dead
    link, and an owner who changed their avatar had no way to make the card follow.

    Pointing at a stable URL that resolves per request buys three things at once:

    - an unfurler that kept the address and comes back later still gets a picture;
    - changing the avatar changes what the card shows, with nothing to bust;
    - unpublishing kills the image, because this route re-asks the same gate the
      document does and stops serving.

    It is NOT a general image proxy, and the reason is structural rather than a
    check: there is no caller-supplied key anywhere in the signature. The only
    input is the system id already in the path, and the bytes are whatever
    `project_system` resolved for THAT view. A key handed in by a caller could
    never be honoured because there is nowhere to hand one in.

    404 for every refusal, with one body, so the reason is never readable. That is
    not an oracle: a card only references this URL when it is already rich, so
    "does this serve" answers exactly what fetching the document would have.
    """
    rich = await _resolve_rich_system(db, raw_system_id)
    if rich is None or not rich.projection.avatar_url:
        raise _no_image()
    target = absolute_url(rich.projection.avatar_url, _origin(request))
    if target is None:
        raise _no_image()
    return _image_redirect(target)


# ---------------------------------------------------------------------------
# Member permalinks: /p/{system_id}/member/{member_id}
# ---------------------------------------------------------------------------


@router.get("/p/{raw_system_id}/member/{raw_member_id}", dependencies=[_RATE])
@router.get(
    "/v1/link-preview/p/{raw_system_id}/member/{raw_member_id}",
    dependencies=[_RATE],
)
async def member_link_preview(
    raw_system_id: str,
    raw_member_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """A member permalink's card, or the generic one. Always 200.

    Name and avatar only. See `build_member_preview` for what is deliberately left
    off and why, and `_resolve_rich_member` for the three extra gates a member card
    has to clear that a system card does not - the sharpest being that the member
    has to be one the projection actually serves.
    """
    origin = _origin(request)
    page_url = f"{origin}/p/{raw_system_id}/member/{raw_member_id}"

    rich = await _resolve_rich_member(db, raw_system_id, raw_member_id)
    if rich is None:
        _count_card(request, "generic")
        return _generic(page_url, token_keyed=False)

    _count_card(request, "member")
    image_url = f"{page_url}/preview-image" if rich.card.avatar_url else None
    return _document(
        build_member_preview(
            name=rich.card.name,
            image_url=image_url,
            page_url=page_url,
            logo_url=_logo(),
        ),
        token_keyed=False,
    )


@router.get(
    "/p/{raw_system_id}/member/{raw_member_id}/preview-image",
    dependencies=[_IMAGE_RATE],
)
@router.get(
    "/v1/link-preview/p/{raw_system_id}/member/{raw_member_id}/preview-image",
    dependencies=[_IMAGE_RATE],
)
async def member_preview_image(
    raw_system_id: str,
    raw_member_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """That member's avatar, resolved fresh on every request.

    The member twin of `system_preview_image`, with the same properties and the same
    reasons, and gated through `_resolve_rich_member` so it cannot serve a picture
    of somebody whose card would not have named them. No caller-supplied key here
    either: the member id in the path is resolved through the view's own projection,
    which is the only thing that decides whether that member is served at all.
    """
    rich = await _resolve_rich_member(db, raw_system_id, raw_member_id)
    if rich is None or not rich.card.avatar_url:
        raise _no_image()
    target = absolute_url(rich.card.avatar_url, _origin(request))
    if target is None:
        raise _no_image()
    return _image_redirect(target)


# ---------------------------------------------------------------------------
# Share links: /s/{token} - always generic, never resolved
# ---------------------------------------------------------------------------


@router.get("/s/{raw_token}", dependencies=[_RATE])
@router.get("/v1/link-preview/s/{raw_token}", dependencies=[_RATE])
@router.get("/s/{raw_token}/member/{raw_member_id}", dependencies=[_RATE])
@router.get(
    "/v1/link-preview/s/{raw_token}/member/{raw_member_id}", dependencies=[_RATE]
)
async def shared_link_preview(request: Request) -> Response:
    """Every `/s/` URL, system or member: the generic card, with nothing looked up.

    This is the "a secret URL never gets a rich preview" rule expressed as code
    that cannot be wrong: the handler takes no path parameters, no database session
    and no view, so there is no path from a share link to a system's name or a
    member's name however that view is configured. The token and member id are in
    the route patterns only so the routes match. The one thing it does take is the
    request, to read WHICH service is asking for the metric; that is a fact about
    the crawler, not about the link, and the token is never read off it.

    A share link's URL is itself the secret - the opaque token exists precisely so
    the system behind it is not learnable from the link - and an unfurl happens on
    the strength of a paste, before the recipient has decided anything, into a
    cache that will outlive the link. A rich card there would hand the details to a
    third party in exactly the case the owner was keeping them from one. That is
    true of a member permalink under a share link as much as of the profile root,
    and more so: it would name one specific person.

    It still returns a card rather than nothing, so a live link, a revoked one, a
    rotated one, an expired one and a string somebody made up all unfurl
    identically.

    No `og:url`: it would copy the bearer token into the card's own metadata, and
    some unfurlers render that text to everyone in the channel.
    """
    _count_card(request, "generic")
    return _generic(None, token_keyed=True)


@router.get("/s/{raw_token}/preview-image", dependencies=[_IMAGE_RATE])
@router.get(
    "/s/{raw_token}/member/{raw_member_id}/preview-image", dependencies=[_IMAGE_RATE]
)
@router.get(
    "/v1/link-preview/s/{raw_token}/preview-image", dependencies=[_IMAGE_RATE]
)
@router.get(
    "/v1/link-preview/s/{raw_token}/member/{raw_member_id}/preview-image",
    dependencies=[_IMAGE_RATE],
)
async def shared_preview_image() -> Response:
    """Always 404, for every `/s/` URL, without looking anything up.

    A `/s/` card is never rich, so it never references an image, so this route has
    nothing legitimate to serve. It is registered anyway - rather than left to fall
    through to the router's own 404 - so that the answer is deliberate and provably
    uniform: it takes no parameters and touches no database, which is what
    guarantees a valid token and an invented one are indistinguishable here.

    That matters because an image route is a tempting oracle. If this resolved the
    token and 404'd only for a missing avatar, the difference between "404, no
    avatar" and "404, no such token" would have been a way to confirm a token is
    live without ever loading the page.
    """
    raise _no_image()
