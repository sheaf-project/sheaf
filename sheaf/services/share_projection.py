"""Turn a resolved share view into public output. The one load-bearing place.

Every anonymous read goes through here and nowhere else builds public payloads.
Two rules are enforced in the QUERY, not just trusted to the caller:

1. `ShareViewMember` with `status == active` is the sole source of who appears.
   A pending row (still inside its grace window) is not visible yet.
2. `Member.never_shareable` is filtered out again here, on top of being rejected
   when a member is added to a view - "we remembered not to add them" is not a
   guarantee, so the projection refuses them regardless.

Output is assembled field-by-field into the dedicated public schemas; an ORM row
is never handed to `model_validate`, so nothing leaks by omission.

Every image URL leaves here through the `*_public` resolvers in `sheaf.files`:
an external image on this surface would make an anonymous visitor's browser
fetch from an owner-chosen host, handing it their address for every page view.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from sheaf.files import resolve_avatar_url_public, resolve_description_urls_public
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue
from sheaf.models.front import Front
from sheaf.models.member import Member, front_members
from sheaf.models.share import (
    ShareItemStatus,
    ShareView,
    ShareViewField,
    ShareViewMember,
)
from sheaf.models.system import PrivacyLevel, System
from sheaf.schemas.public_profile import (
    PublicFrontingMember,
    PublicFrontingView,
    PublicMemberView,
    PublicSystemView,
)
from sheaf.services.custom_fields import field_value_plaintext
from sheaf.services.members import (
    member_description_plaintext,
    member_name_plaintext,
)


def _active_member_filter(stmt: Select, view: ShareView) -> Select:
    """Restrict a select to members this view actively exposes to the PUBLIC tier.

    The one place the "who is in this view AND allowed at this audience" rule
    lives, so the full-row list, the id set, and the count can never drift apart.
    Two independent guards, both in SQL:

    - `never_shareable` - a secret member never projects, even if a stale
      membership row survives.
    - `privacy == public` - `member.privacy` is the member's exposure CEILING.
      Every grant that exists today (public profile and unlisted link) is
      PUBLIC-tier, so only members the owner marked public appear; a member left
      at the default `private`, or set to `friends`, never shows here even when
      they were deliberately added to the view. This is what makes a mis-added
      default member structurally unable to leak. When the (parked) friends tier
      lands, this filter becomes audience-parameterised: a friends grant would
      admit `friends` as well as `public`.

    The tenant predicate is redundant with the write paths, which refuse a
    member from another system at the point of adding. It is here anyway: this
    is the query that feeds anonymous readers, so it does not get to rely on
    every writer having been correct.
    """
    return stmt.join(ShareViewMember, ShareViewMember.member_id == Member.id).where(
        ShareViewMember.view_id == view.id,
        ShareViewMember.status == ShareItemStatus.ACTIVE.value,
        Member.system_id == view.system_id,
        Member.never_shareable.is_(False),
        Member.privacy == PrivacyLevel.PUBLIC,
    )


async def _active_members(db: AsyncSession, view: ShareView) -> list[Member]:
    """Full member rows actively exposed by this view."""
    result = await db.execute(_active_member_filter(select(Member), view))
    return list(result.scalars().unique().all())


async def _active_member_ids(db: AsyncSession, view: ShareView) -> set[uuid.UUID]:
    """Just the ids - for the count and the fronting in-view check, without
    loading and decrypting full member rows."""
    result = await db.execute(_active_member_filter(select(Member.id), view))
    return set(result.scalars().all())


async def _active_member_count(db: AsyncSession, view: ShareView) -> int:
    result = await db.execute(
        _active_member_filter(select(func.count(Member.id)), view)
    )
    return int(result.scalar_one())


async def _exposed_fields(
    db: AsyncSession, view: ShareView
) -> dict[uuid.UUID, str]:
    """{field_definition_id: name} for the custom fields this view exposes.

    Tenant-pinned for the same reason as `_active_member_filter`.
    """
    result = await db.execute(
        select(CustomFieldDefinition.id, CustomFieldDefinition.name)
        .join(ShareViewField, ShareViewField.field_id == CustomFieldDefinition.id)
        .where(
            ShareViewField.view_id == view.id,
            ShareViewField.status == ShareItemStatus.ACTIVE.value,
            CustomFieldDefinition.system_id == view.system_id,
        )
    )
    return {row[0]: row[1] for row in result}


async def _field_values_by_member(
    db: AsyncSession, member_ids: list[uuid.UUID], field_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[CustomFieldValue]]:
    if not member_ids or not field_ids:
        return {}
    result = await db.execute(
        select(CustomFieldValue).where(
            CustomFieldValue.member_id.in_(member_ids),
            CustomFieldValue.field_id.in_(field_ids),
        )
    )
    out: dict[uuid.UUID, list[CustomFieldValue]] = {}
    for v in result.scalars().all():
        out.setdefault(v.member_id, []).append(v)
    return out


def _sort_key(m: Member) -> str:
    """Alphabetical by the shown name. Insertion order would leak internal
    structure; the display name (or decrypted name) is what a visitor sees."""
    return (m.display_name or member_name_plaintext(m) or "").casefold()


def _member_view(
    m: Member,
    *,
    include_bio: bool,
    field_names: dict[uuid.UUID, str],
    values: list[CustomFieldValue],
) -> PublicMemberView:
    fields: dict[str, object] = {}
    for v in values:
        name = field_names.get(v.field_id)
        if name is not None:
            # Field values are text by contract: the public page renders them
            # as plain strings in a badge, never as markdown or a URL, so they
            # need no image handling.
            fields[name] = field_value_plaintext(v)
    return PublicMemberView(
        id=str(m.id),
        name=member_name_plaintext(m),
        display_name=m.display_name,
        pronouns=m.pronouns,
        avatar_url=resolve_avatar_url_public(m.avatar_url),
        banner_url=resolve_avatar_url_public(m.banner_url),
        color=m.color,
        bio=(
            resolve_description_urls_public(member_description_plaintext(m))
            if include_bio
            else None
        ),
        fields=fields,
    )


async def project_members(db: AsyncSession, view: ShareView) -> list[PublicMemberView]:
    members = await _active_members(db, view)
    members.sort(key=_sort_key)
    field_names = await _exposed_fields(db, view)
    values_by_member = await _field_values_by_member(
        db, [m.id for m in members], set(field_names)
    )
    return [
        _member_view(
            m,
            include_bio=view.include_bio,
            field_names=field_names,
            values=values_by_member.get(m.id, []),
        )
        for m in members
    ]


async def project_system(
    db: AsyncSession, view: ShareView, system: System
) -> PublicSystemView:
    member_count = await _active_member_count(db, view)
    return PublicSystemView(
        id=str(system.id),
        name=system.name,
        # Rendered as markdown on the public page, same as a member bio - and
        # the resolve pass is also what signs an internal image ref here, which
        # the raw column value never was.
        description=resolve_description_urls_public(system.description),
        avatar_url=resolve_avatar_url_public(system.avatar_url),
        color=system.color,
        tag=system.tag,
        member_count=member_count,
    )


async def project_fronting(
    db: AsyncSession, view: ShareView, system: System
) -> PublicFrontingView:
    """Who is publicly fronting right now.

    Members named here must be BOTH in this view and currently fronting. A
    member fronting but not in this view contributes to `hidden_count` (when
    the view allows it). Never-shareable and fronting-private members are
    excluded from the count too: their front state must not propagate at all,
    so even "someone is fronting" would be a leak.
    """
    if not view.include_fronting:
        return PublicFrontingView(members=[], hidden_count=0)

    # Current open fronts for the system, with their members and start times.
    rows = await db.execute(
        select(Member, Front.started_at)
        .join(front_members, front_members.c.member_id == Member.id)
        .join(Front, Front.id == front_members.c.front_id)
        .where(Front.system_id == system.id, Front.ended_at.is_(None))
    )
    # A member could be in more than one open front (co-front chains); keep the
    # earliest start as "fronting since".
    since_by_member: dict[uuid.UUID, object] = {}
    members_by_id: dict[uuid.UUID, Member] = {}
    for member, started_at in rows:
        members_by_id[member.id] = member
        prev = since_by_member.get(member.id)
        if prev is None or (started_at is not None and started_at < prev):
            since_by_member[member.id] = started_at

    in_view = await _active_member_ids(db, view)

    named: list[PublicFrontingMember] = []
    hidden = 0
    for mid, member in members_by_id.items():
        if member.never_shareable or member.fronting_private:
            # Front state does not propagate for these members, not even as a
            # number.
            continue
        if mid in in_view:
            started = since_by_member.get(mid)
            named.append(
                PublicFrontingMember(
                    id=str(member.id),
                    name=member_name_plaintext(member),
                    display_name=member.display_name,
                    pronouns=member.pronouns,
                    avatar_url=resolve_avatar_url_public(member.avatar_url),
                    color=member.color,
                    since=started.isoformat() if started is not None else None,
                )
            )
        else:
            hidden += 1

    named.sort(key=lambda pm: (pm.display_name or pm.name or "").casefold())
    return PublicFrontingView(
        members=named,
        hidden_count=hidden if view.fronting_show_count else 0,
    )
