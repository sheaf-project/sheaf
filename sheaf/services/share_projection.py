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
Those resolvers take the owning account's id and sign only keys from that
account's namespace, so every function here threads an `owner_id` down to them.

Payload identity is deliberately ONE name field. `_shown_name` decides what a
visitor reads and nothing else reaches a schema, so a canonical name behind a
display name never leaves the account.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from sheaf.files import resolve_avatar_url_public, resolve_description_urls_public
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.member import Member, front_members, group_members
from sheaf.models.pending_action import (
    PendingAction,
    PendingActionStatus,
    PendingActionType,
)
from sheaf.models.relationship import MemberRelationship, RelationshipType
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
    PublicGroupMember,
    PublicGroupsView,
    PublicGroupView,
    PublicMemberView,
    PublicRelationship,
    PublicRelationshipEndpoint,
    PublicRelationshipsView,
    PublicSystemView,
)
from sheaf.services.custom_fields import field_value_plaintext
from sheaf.services.members import (
    member_description_plaintext,
    member_name_plaintext,
)
from sheaf.services.relationships import endpoint_labels


def _not_deletion_queued(
    action_type: PendingActionType,
    target_column: ColumnElement,
    system_id: uuid.UUID,
) -> ColumnElement[bool]:
    """SQL predicate: no still-pending safeguarded delete is queued for this row.

    Safeguarded deletion is a two-step act. The owner presses delete, a
    `PendingAction` is written, and the row itself survives until the finalize
    sweep runs at the end of the grace window - the window exists so the owner
    can change their mind, not so the world gets a last look. Without this
    predicate the public surface kept serving a member (or group, or field) for
    the entire window after its owner asked for it to be gone, which is the
    opposite of what "exposing waits, un-exposing is instant" promises: this is
    an un-exposing act, so it lands NOW and the grace window applies only to
    the destruction of the data.

    Correlated rather than pre-fetched into an id set, for the reason every
    other rule on this surface lives in the query: an id set computed by one
    caller is an id set a second caller can forget to compute.
    """
    return ~(
        select(PendingAction.id)
        .where(
            PendingAction.system_id == system_id,
            PendingAction.action_type == action_type.value,
            PendingAction.target_id == target_column,
            PendingAction.status == PendingActionStatus.PENDING.value,
        )
        .exists()
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

    - `archived_at IS NULL` - archiving is a reversible soft-hide, and it hides
      the member from the owner's own roster, switcher and pickers. A member the
      owner has put away is not one they are still publishing to strangers, so
      the public surface honours it at the same instant the private one does.
      Nothing is re-staged on the way back: archiving does not touch the
      `ShareViewMember` row, so unarchiving returns the member to the profile
      immediately rather than sending them back through the grace window. That
      asymmetry is deliberate and is the same one that governs every flag here -
      going dark is instant, and coming back is not a new exposure decision
      because the owner never revoked the curation.
    - no queued deletion - see `_not_deletion_queued`.

    The tenant predicate is redundant with the write paths, which refuse a
    member from another system at the point of adding. It is here anyway: this
    is the query that feeds anonymous readers, so it does not get to rely on
    every writer having been correct.

    Because `_active_member_ids` composes out of this same filter, and both
    `projectable_relationships` and `project_groups` gate on that id set, edges
    and group rosters inherit every rule above without restating any of them.
    An archived member therefore cannot survive as the endpoint of a published
    edge or as a name in a group's roster.
    """
    return stmt.join(ShareViewMember, ShareViewMember.member_id == Member.id).where(
        ShareViewMember.view_id == view.id,
        ShareViewMember.status == ShareItemStatus.ACTIVE.value,
        Member.system_id == view.system_id,
        Member.never_shareable.is_(False),
        Member.privacy == PrivacyLevel.PUBLIC,
        Member.archived_at.is_(None),
        _not_deletion_queued(
            PendingActionType.MEMBER_DELETE, Member.id, view.system_id
        ),
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

    THE choke point for custom-field exposure, in the same spirit as
    `_active_member_filter`: one place decides "is this field in the view AND
    allowed at this audience", so the projected payloads and the owner-side
    audit can never disagree about what a visitor reads. Two independent
    guards, both in SQL:

    - an ACTIVE `ShareViewField` row - selection is the owner's curation, and a
      pending row (still inside its grace window) is not visible yet;
    - `privacy == public` on the DEFINITION - `CustomFieldDefinition.privacy`
      is the field's exposure CEILING. Every grant that exists today is
      PUBLIC-tier, so a definition left at the default `private`, or set to
      `friends`, is served to nobody even though the owner deliberately
      selected it into this view.

    Both are necessary, and neither is redundant with the other. Selection
    without the ceiling is what shipped before this filter existed: a field
    appeared because somebody had picked it, whatever the level next to it
    said. The ceiling without selection would publish a field into every view
    at once. Requiring both is what makes a definition added to a view "on the
    way to publishing it" rather than "published", which is the same shape as
    a member sitting in a view at `private`.

    The level is per-DEFINITION and therefore applies to that field on every
    member; there is no per-member-per-field setting, on purpose. Withholding
    one member's value for one field is what the member's own privacy level and
    simply not filling the field in are for.

    A third guard joins them, on the same footing: a definition with a queued
    safeguarded delete stops being served at once. Field deletion IS
    safeguarded (`PendingActionType.FIELD_DELETE`, category `fields`), so the
    row outlives the request that deleted it, and `_not_deletion_queued`
    explains why the public surface must not.

    Tenant-pinned for the same reason as `_active_member_filter`: this query
    feeds anonymous readers, so it does not get to rely on every writer having
    been correct.
    """
    result = await db.execute(
        select(CustomFieldDefinition.id, CustomFieldDefinition.name)
        .join(ShareViewField, ShareViewField.field_id == CustomFieldDefinition.id)
        .where(
            ShareViewField.view_id == view.id,
            ShareViewField.status == ShareItemStatus.ACTIVE.value,
            CustomFieldDefinition.system_id == view.system_id,
            CustomFieldDefinition.privacy == PrivacyLevel.PUBLIC,
            _not_deletion_queued(
                PendingActionType.FIELD_DELETE,
                CustomFieldDefinition.id,
                view.system_id,
            ),
        )
    )
    return {row[0]: row[1] for row in result}


async def projectable_fields(
    db: AsyncSession, view: ShareView
) -> dict[uuid.UUID, str]:
    """The custom fields this view would serve right now, {id: name}.

    A public name for `_exposed_fields` so the owner-side audit counts through
    exactly the filter the member cards are built from, the way
    `projectable_relationships` and `projectable_groups` are shared. A count
    the owner reads off the sharing screen has to be the count a visitor could
    reconstruct; two queries with the same intent drift, and the drift here
    would be an audit quietly over-reporting what is published.

    Deliberately NOT gated on `include_members`, unlike the projection that
    consumes it. With the roster off nothing renders these anywhere, and
    `project_members` returns early long before it asks - but the audit says
    "no member list" beside this number, and zeroing the fields as well would
    read as "your curation is gone" for a flag that destroyed nothing. Same
    reasoning as the member count it sits next to.
    """
    return await _exposed_fields(db, view)


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


def _shown_name(m: Member) -> str:
    """What a visitor actually reads for this member: the display name if there
    is one, otherwise the decrypted name."""
    return m.display_name or member_name_plaintext(m) or ""


def _sort_key(m: Member) -> str:
    """Alphabetical by the shown name. Insertion order would leak internal
    structure; the display name (or decrypted name) is what a visitor sees."""
    return _shown_name(m).casefold()


def _member_view(
    m: Member,
    *,
    include_bio: bool,
    field_names: dict[uuid.UUID, str],
    values: list[CustomFieldValue],
    owner_id: uuid.UUID,
) -> PublicMemberView:
    """One member card, carrying exactly one name.

    `name` is `_shown_name` - the display name when there is one, the decrypted
    name only as a fallback. The payload used to carry both, which meant a
    member who had set a display name specifically so strangers would not read
    their canonical name had that canonical name sitting in the JSON anyway,
    one field along, for any scraper that asked. A display name is a request
    not to be called something else; publishing the something else next to it
    makes the request decorative.
    """
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
        name=_shown_name(m),
        pronouns=m.pronouns,
        avatar_url=resolve_avatar_url_public(m.avatar_url, owner_id),
        banner_url=resolve_avatar_url_public(m.banner_url, owner_id),
        color=m.color,
        bio=(
            resolve_description_urls_public(
                member_description_plaintext(m), owner_id
            )
            if include_bio
            else None
        ),
        fields=fields,
    )


async def project_members(
    db: AsyncSession,
    view: ShareView,
    *,
    owner_id: uuid.UUID,
    only_id: uuid.UUID | None = None,
) -> list[PublicMemberView]:
    """The member cards this view serves, in display order.

    Gated on `include_members` HERE rather than only at the API layer, for the
    same reason `never_shareable` is refused in the query rather than only at
    the point of adding: the flag is the whole roster's on/off switch, so the
    one function that builds member payloads is where it has to bite. The
    endpoints 404 on top of this so an off flag is not separately probeable.

    `only_id` narrows the result to a single member for the permalink route.
    It is a filter on this function rather than a second fetch elsewhere on
    purpose: a permalink must serve exactly the card the list would have
    served, no more, and the way to guarantee that is for there to be only one
    place that builds it.
    """
    if not view.include_members:
        return []
    members = await _active_members(db, view)
    if only_id is not None:
        members = [m for m in members if m.id == only_id]
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
            owner_id=owner_id,
        )
        for m in members
    ]


async def projectable_relationships(
    db: AsyncSession,
    view: ShareView,
    *,
    active_ids: set[uuid.UUID] | None = None,
) -> list[tuple[MemberRelationship, RelationshipType]]:
    """The member edges this view would serve right now, with their types.

    THE choke point for relationship exposure, and the reason it is a single
    function rather than a filter each caller reassembles: the owner-side audit
    counts exactly what an anonymous visitor gets, because both go through
    here. Three gates, all in the query:

    - the view's `include_relationships` flag (an off flag yields nothing here,
      and the API layer 404s the endpoint outright rather than serving an empty
      list, so "does this profile share relationships?" is not separately
      probeable);
    - `visibility == public` on the EDGE itself - private (the default) and
      friends-level edges never leave the owner's account;
    - and both endpoints inside `_active_member_ids`, which is what composes
      this on top of the member ceiling. That is the load-bearing part: an edge
      is the one payload that names two people at once, so it can never be the
      thing that outs an endpoint the view does not already publish in full.
      Marking an edge public is therefore a request, not an override.

    Group edges are deliberately absent: nothing projects them at all.

    `include_members` gates this too, and that follows from the third bullet
    rather than being a separate policy: with the roster off, the view does not
    publish ANY endpoint in full, so no edge can clear the bar. It matters
    practically as well - an endpoint here is deliberately just an id and a
    name, meant to be joined against /members for anything richer, so with
    /members gone the name in the edge would be the only thing a visitor got,
    which is precisely the "edge outs an endpoint" case this function exists to
    make impossible.
    """
    if not view.include_relationships or not view.include_members:
        return []
    if active_ids is None:
        active_ids = await _active_member_ids(db, view)
    # An edge needs two projected endpoints, so one lonely member cannot have
    # one; skip the query entirely.
    if len(active_ids) < 2:
        return []

    result = await db.execute(
        select(MemberRelationship, RelationshipType)
        .join(
            RelationshipType,
            RelationshipType.id == MemberRelationship.relationship_type_id,
        )
        .where(
            # Redundant with the endpoint filter below, and here for the same
            # reason the member projection pins its tenant: this query feeds
            # anonymous readers and does not get to assume every writer was
            # correct.
            MemberRelationship.system_id == view.system_id,
            MemberRelationship.visibility == PrivacyLevel.PUBLIC,
            MemberRelationship.source_id.in_(active_ids),
            MemberRelationship.target_id.in_(active_ids),
        )
    )
    return list(result.all())


async def project_relationships(
    db: AsyncSession, view: ShareView
) -> PublicRelationshipsView:
    """Published edges between members this view shows, as a flat list."""
    members = await _active_members(db, view)
    # Same rows `_active_member_ids` would return (identical filter), reused so
    # the names and the id gate cannot disagree about who is in the view.
    by_id = {m.id: m for m in members}
    pairs = await projectable_relationships(db, view, active_ids=set(by_id))

    out: list[PublicRelationship] = []
    for edge, rtype in pairs:
        source = by_id.get(edge.source_id)
        target = by_id.get(edge.target_id)
        if source is None or target is None:
            continue
        source_label, target_label = endpoint_labels(
            symmetry=rtype.symmetry,
            forward_label=rtype.forward_label,
            reverse_label=rtype.reverse_label,
            mutual=edge.mutual,
        )
        out.append(
            PublicRelationship(
                id=str(edge.id),
                type_name=rtype.name,
                type_color=rtype.color,
                source=PublicRelationshipEndpoint(
                    id=str(source.id), name=_shown_name(source)
                ),
                target=PublicRelationshipEndpoint(
                    id=str(target.id), name=_shown_name(target)
                ),
                source_label=source_label,
                target_label=target_label,
                mutual=edge.mutual,
            )
        )

    # Sorted by what a visitor can already read, never by insertion order: the
    # order edges were created in leaks internal structure (which relationships
    # the system added first, and therefore which it considers foundational) -
    # the same reason `_sort_key` exists for members.
    out.sort(
        key=lambda r: (
            r.type_name.casefold(),
            r.source.name.casefold(),
            r.target.name.casefold(),
        )
    )
    return PublicRelationshipsView(relationships=out)


async def projectable_groups(
    db: AsyncSession, view: ShareView
) -> list[Group]:
    """The groups this view would serve right now.

    THE choke point for group exposure, and a single function for the same
    reason `projectable_relationships` is one: the owner-side audit counts
    exactly what an anonymous visitor gets, because both come through here.
    Two gates, both in the query:

    - the view's `include_groups` flag (an off flag yields nothing, and the API
      layer 404s the endpoint outright rather than serving an empty list, so
      "does this profile show groups?" is not separately probeable);
    - `privacy == public` on the GROUP itself - private (the default) and
      friends-level groups never leave the owner's account;
    - no queued safeguarded delete. Group deletion IS safeguarded
      (`PendingActionType.GROUP_DELETE`, category `groups`), so the row
      outlives the request that deleted it; `_not_deletion_queued` explains why
      the public surface must not keep serving it in the meantime.

    There is deliberately no per-view group allowlist and no third gate on who
    is IN the group. A group's published payload is what the owner wrote about
    the group; its roster is assembled in `project_groups` as an intersection
    with the members this view already serves, so a public group can never be
    the thing that names somebody new.

    The tenant predicate is redundant with the write paths and is here anyway,
    for the reason every query on this surface pins its tenant: it feeds
    anonymous readers and does not get to assume every writer was correct.
    """
    if not view.include_groups:
        return []
    result = await db.execute(
        select(Group).where(
            Group.system_id == view.system_id,
            Group.privacy == PrivacyLevel.PUBLIC,
            _not_deletion_queued(
                PendingActionType.GROUP_DELETE, Group.id, view.system_id
            ),
        )
    )
    return list(result.scalars().unique().all())


async def project_groups(
    db: AsyncSession, view: ShareView, *, owner_id: uuid.UUID
) -> PublicGroupsView:
    """Published groups, each with the part of its roster this view shows."""
    groups = await projectable_groups(db, view)
    if not groups:
        return PublicGroupsView(groups=[])

    # The same rows `project_members` serves (identical filter, and skipped
    # entirely when the roster is off), reused so a group's member list and the
    # /members list cannot disagree about who is in the view or what they are
    # called.
    members = await _active_members(db, view) if view.include_members else []
    by_id = {m.id: m for m in members}

    roster: dict[uuid.UUID, list[Member]] = {}
    if by_id:
        rows = await db.execute(
            select(group_members.c.group_id, group_members.c.member_id).where(
                group_members.c.group_id.in_([g.id for g in groups]),
                group_members.c.member_id.in_(by_id),
            )
        )
        for group_id, member_id in rows:
            member = by_id.get(member_id)
            if member is not None:
                roster.setdefault(group_id, []).append(member)

    out: list[PublicGroupView] = []
    for group in groups:
        shown = roster.get(group.id, [])
        shown.sort(key=_sort_key)
        out.append(
            PublicGroupView(
                id=str(group.id),
                name=group.name,
                # Group descriptions are markdown and can carry image refs, so
                # they take the same public resolve pass as a system
                # description or a member bio: internal refs get signed
                # same-origin URLs and external ones are hidden, because an
                # anonymous visitor's browser must never be sent to an
                # owner-chosen host. The `owner_id` matters most here of
                # anywhere - a group description is the one markdown field the
                # group write API never ran an ownership pass over, so this is
                # the guard that stops a foreign storage key stored by an old
                # write or a foreign importer from being signed into a live
                # cross-tenant capability.
                description=resolve_description_urls_public(
                    group.description, owner_id
                ),
                color=group.color,
                members=[
                    PublicGroupMember(id=str(m.id), name=_shown_name(m))
                    for m in shown
                ],
            )
        )

    # By name, never by insertion order, for the reason `_sort_key` exists:
    # creation order says which groups the system considers foundational.
    out.sort(key=lambda g: g.name.casefold())
    return PublicGroupsView(groups=out)


async def project_system(
    db: AsyncSession, view: ShareView, system: System
) -> PublicSystemView:
    # A roster this view refuses to serve must not be countable either:
    # "23 members you cannot see" is still a fact about the system, and it is
    # exactly the fact somebody turning the roster off was trying not to
    # publish. Null, not zero - zero would be a claim, and a false one.
    member_count = (
        await _active_member_count(db, view) if view.include_members else None
    )
    return PublicSystemView(
        id=str(system.id),
        name=system.name,
        # Rendered as markdown on the public page, same as a member bio - and
        # the resolve pass is also what signs an internal image ref here, which
        # the raw column value never was. `system.user_id` IS the owning
        # account, so it is what the signer checks the keys against.
        description=resolve_description_urls_public(
            system.description, system.user_id
        ),
        avatar_url=resolve_avatar_url_public(system.avatar_url, system.user_id),
        color=system.color,
        tag=system.tag,
        member_count=member_count,
        member_permalinks=view.member_permalinks,
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

    Archived and deletion-queued members are excluded IN THE QUERY below rather
    than left to the in-view check, and that is load-bearing. This is the one
    surface that walks open fronts on its own instead of composing out of
    `_active_member_filter`, so the rule has to be restated here or a member
    the owner archived (or asked to delete) would keep announcing their
    presence - and if they happened to fall outside the view, they would do it
    as an anonymous `hidden_count` increment, which is exactly the "someone is
    fronting" leak the never-shareable exclusion exists to prevent. Excluded
    from the naming AND the count, for that reason.

    Deliberately NOT gated on `include_members`, unlike the roster, the group
    rosters and the relationship edges. Fronting is its own surface with its
    own flag, its own deliberately-reduced card, and its own reason to be on -
    "who is around right now" is a thing people publish without wanting a
    directory next to it. Turning the roster off is therefore not a
    member-anonymity switch and nothing here should pretend it is; an owner who
    wants nobody named anywhere turns this off as well.
    """
    if not view.include_fronting:
        return PublicFrontingView(members=[], hidden_count=0)

    # Current open fronts for the system, with their members and start times.
    rows = await db.execute(
        select(Member, Front.started_at)
        .join(front_members, front_members.c.member_id == Member.id)
        .join(Front, Front.id == front_members.c.front_id)
        .where(
            Front.system_id == system.id,
            Front.ended_at.is_(None),
            Member.archived_at.is_(None),
            _not_deletion_queued(
                PendingActionType.MEMBER_DELETE, Member.id, system.id
            ),
        )
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
                    # One name, the shown one - same rule and same reasoning as
                    # `_member_view`. This surface is polled repeatedly, so a
                    # canonical name here is the one a scraper would collect
                    # most cheaply of all.
                    name=_shown_name(member),
                    pronouns=member.pronouns,
                    avatar_url=resolve_avatar_url_public(
                        member.avatar_url, system.user_id
                    ),
                    color=member.color,
                    since=started.isoformat() if started is not None else None,
                )
            )
        else:
            hidden += 1

    named.sort(key=lambda pm: pm.name.casefold())
    return PublicFrontingView(
        members=named,
        hidden_count=hidden if view.fronting_show_count else 0,
    )
