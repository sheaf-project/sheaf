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

Shape of every projection here: run the queries on the event loop, then hand
the already-loaded rows to ONE `asyncio.to_thread` call that does the whole
CPU-bound pass (decrypt, markdown resolve, card assembly, sort). See
`_build_member_views` for why.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import NamedTuple

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


async def projectable_member_count(
    db: AsyncSession, view: ShareView
) -> int | None:
    """How many members this view would serve right now, or None with the
    roster off.

    A public name for `_active_member_count`, in the same spirit as
    `projectable_fields` / `projectable_relationships` / `projectable_groups`:
    the owner-side audit counts through exactly the filter the member cards are
    built from, so a number the owner reads off the sharing screen is a number
    a visitor could reconstruct. The audit's other count of members is the
    CURATED one (how many rows the owner put in the view), and the two are
    different questions on purpose - this one answers "how many people is this
    actually showing".

    None rather than 0 when `include_members` is off, matching what
    `project_system` puts in `member_count` for the same reason: a roster the
    view refuses to serve must not be countable, and zero would be a claim.
    """
    if not view.include_members:
        return None
    return await _active_member_count(db, view)


# The reasons a member sitting in a view is not actually being served. A small
# fixed vocabulary so the client can render each one rather than re-deriving
# any of them from other fields - which is what it used to do, off member
# privacy alone, quietly missing the archived and deletion-queued cases.
#
# Order is precedence, most permanent first: a member can be several of these
# at once, and the owner needs to be told the one that will still be true after
# the others clear. `pending` sits last for exactly that reason - it is the
# only entry here that resolves by itself, so anything else about the member is
# the more useful answer, and the row's own `status` still says "pending"
# beside it.
NOT_SERVED_REASONS: tuple[str, ...] = (
    "never_shareable",
    "deletion_queued",
    "archived",
    "private",
    "pending",
)


class MemberServiceState(NamedTuple):
    """Whether one curated member is actually being served, and why not.

    `reason` is one of `NOT_SERVED_REASONS`, or None - which means "served"
    when `served` is True, and "excluded for a reason this classifier cannot
    name" when it is False (see `member_service_states`).
    """

    served: bool
    reason: str | None


async def member_service_states(
    db: AsyncSession, view: ShareView, rows: list[ShareViewMember]
) -> dict[uuid.UUID, MemberServiceState]:
    """Per member row: is this person actually appearing, and if not, why not.

    The owner-side answer to "will this person actually appear?", computed
    where the answer lives instead of guessed at by the client. It composes out
    of `_active_member_ids` rather than restating any of that filter's
    predicates: the served set IS the projection's own, so this cannot claim
    somebody shows when the projection would drop them, however that filter
    grows later.

    Only the misses need explaining, and they take ONE further query - the
    member row plus a correlated "is a delete queued for you" existence test,
    the same `_not_deletion_queued` predicate the filter uses, negated. The
    pending case needs no query at all: it is the row's own status.

    A reason of None on an unserved member would mean this classifier and
    `_active_member_filter` had drifted apart, so it is left as None rather
    than guessed at - the client then says the member will not show without
    inventing a reason for it.
    """
    served = await _active_member_ids(db, view)
    states: dict[uuid.UUID, MemberServiceState] = {}
    misses: set[uuid.UUID] = set()
    for row in rows:
        if row.member_id in served:
            states[row.member_id] = MemberServiceState(True, None)
        else:
            misses.add(row.member_id)
    if not misses:
        return states

    pending_ids = {
        row.member_id
        for row in rows
        if row.status != ShareItemStatus.ACTIVE.value
    }
    result = await db.execute(
        select(
            Member.id,
            Member.never_shareable,
            Member.privacy,
            Member.archived_at,
            ~_not_deletion_queued(
                PendingActionType.MEMBER_DELETE, Member.id, view.system_id
            ),
        ).where(
            Member.id.in_(misses),
            # Tenant-pinned like every other query in this module.
            Member.system_id == view.system_id,
        )
    )
    for member_id, never, privacy, archived_at, deletion_queued in result:
        if never:
            reason = "never_shareable"
        elif deletion_queued:
            reason = "deletion_queued"
        elif archived_at is not None:
            reason = "archived"
        elif privacy != PrivacyLevel.PUBLIC:
            reason = "private"
        elif member_id in pending_ids:
            reason = "pending"
        else:
            reason = None
        states[member_id] = MemberServiceState(False, reason)
    # A row whose member is not this system's (impossible while the FK stands)
    # is not served and has no nameable reason.
    for member_id in misses:
        states.setdefault(member_id, MemberServiceState(False, None))
    return states


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


def _build_member_views(
    members: list[Member],
    *,
    include_bio: bool,
    field_names: dict[uuid.UUID, str],
    values_by_member: dict[uuid.UUID, list[CustomFieldValue]],
    owner_id: uuid.UUID,
) -> list[PublicMemberView]:
    """The whole roster's cards, built off the event loop.

    THE reason this is a separate sync function rather than a comprehension in
    `project_members`: everything it does is CPU-bound and unbounded by the
    roster's owner. Each card decrypts a name (and, sorting, decrypts every
    name again), decrypts a description, and runs a full CommonMark parse over
    it via `resolve_description_urls_public`. A bio may be 20k characters, and
    a roster may be large, so a single hostile profile is a multi-hundred-
    millisecond block of pure Python - and an async worker that is blocking is
    not serving anybody else's request either, public or authenticated. One
    `asyncio.to_thread` hop per projection moves that cost onto a worker
    thread, where it competes for the GIL instead of parking the loop.

    It takes ALREADY-LOADED rows and plain values on purpose. Nothing here may
    touch the AsyncSession: every query runs on the loop before the hop, and
    only column attributes of these rows are read (never a relationship, which
    would try to lazy-load from a thread with no greenlet context). The rows
    also cannot be expired - the session is built with `expire_on_commit=False`
    - so a column read here can never turn into IO. The crypto is pure
    functions over bytes and a process-wide key, and the markdown parser is a
    module-level instance that builds fresh per-call state, so both are safe to
    run from several threads at once.
    """
    ordered = sorted(members, key=_sort_key)
    return [
        _member_view(
            m,
            include_bio=include_bio,
            field_names=field_names,
            values=values_by_member.get(m.id, []),
            owner_id=owner_id,
        )
        for m in ordered
    ]


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
    field_names = await _exposed_fields(db, view)
    values_by_member = await _field_values_by_member(
        db, [m.id for m in members], set(field_names)
    )
    # Queries done; the decrypt-and-render pass goes to a thread. The permalink
    # route lands here too (`only_id`), so a single hostile card is bounded the
    # same way the whole roster is.
    return await asyncio.to_thread(
        _build_member_views,
        members,
        include_bio=view.include_bio,
        field_names=field_names,
        values_by_member=values_by_member,
        owner_id=owner_id,
    )


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
    make impossible. The API layer 404s on that flag as well, for the same
    reason it 404s on `include_relationships`: the empty list this returns is
    an answer, and "is the roster off?" is not a question this surface takes.
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


def _build_relationship_views(
    by_id: dict[uuid.UUID, Member],
    pairs: list[tuple[MemberRelationship, RelationshipType]],
) -> PublicRelationshipsView:
    """Edge payloads, built off the event loop - see `_build_member_views`.

    Every endpoint name is a `_shown_name`, so a busy graph decrypts once per
    edge end and then sorts on those names; same CPU-bound shape as the roster,
    same thread hop. Only column attributes of the already-loaded edge, type,
    and member rows are read here.
    """
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


async def project_relationships(
    db: AsyncSession, view: ShareView
) -> PublicRelationshipsView:
    """Published edges between members this view shows, as a flat list."""
    members = await _active_members(db, view)
    # Same rows `_active_member_ids` would return (identical filter), reused so
    # the names and the id gate cannot disagree about who is in the view.
    by_id = {m.id: m for m in members}
    pairs = await projectable_relationships(db, view, active_ids=set(by_id))
    return await asyncio.to_thread(_build_relationship_views, by_id, pairs)


async def _projectable_group_rosters(
    db: AsyncSession, view: ShareView
) -> list[tuple[Group, list[Member]]]:
    """The groups this view would serve right now, each with its roster.

    THE choke point for group exposure, and a single function for the same
    reason `projectable_relationships` is one: the owner-side audit counts
    exactly what an anonymous visitor gets, because both come through here.
    `projectable_groups` and `project_groups` are both thin wrappers over this,
    so the count and the payload cannot disagree about which groups are served
    - they used to be two functions applying two different rules, which is
    precisely how the count could over-report.

    Four gates. The first three are in the query:

    - the view's `include_groups` flag (an off flag yields nothing, and the API
      layer 404s the endpoint outright rather than serving an empty list, so
      "does this profile show groups?" is not separately probeable);
    - `privacy == public` on the GROUP itself - private (the default) and
      friends-level groups never leave the owner's account;
    - no queued safeguarded delete. Group deletion IS safeguarded
      (`PendingActionType.GROUP_DELETE`, category `groups`), so the row
      outlives the request that deleted it; `_not_deletion_queued` explains why
      the public surface must not keep serving it in the meantime.

    The fourth is the roster itself: a public group whose intersection with the
    members this view serves is EMPTY is dropped. A group with nobody in it, as
    far as this view is concerned, is a name and a description with nothing
    behind them - it tells a visitor that a "Littles" or a "Trauma holders"
    exists in this system while the view was set up to name nobody in it, which
    is a fact about the system the owner did not publish by publishing a
    roster. Dropped from the PAYLOAD, not merely hidden by the client: a
    scraper reads the JSON, so a group the page does not draw must not be in
    the response either.

    There is deliberately no per-view group allowlist and no gate on WHO is in
    the group beyond that intersection: a public group can never be the thing
    that names somebody new, because its roster is built from the members the
    view already serves.

    With `include_members` off every roster is empty, so this returns nothing
    and the groups endpoint serves `{"groups": []}`. The flag oracle is
    unchanged - the endpoint still 404s on `include_groups` being off, and an
    empty list is what a view with no servable group has always returned - so
    "does this profile show groups?" is still not separately answerable.

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
    groups = list(result.scalars().unique().all())
    if not groups:
        return []

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

    return [(g, roster[g.id]) for g in groups if roster.get(g.id)]


async def projectable_groups(
    db: AsyncSession, view: ShareView
) -> list[Group]:
    """The groups this view would serve right now, for the owner-side count.

    Exactly the groups `project_groups` publishes and no others, because both
    come through `_projectable_group_rosters` - including its empty-roster
    rule, which is the part that used to be applied by one and not the other.
    """
    return [group for group, _ in await _projectable_group_rosters(db, view)]


def _build_group_views(
    pairs: list[tuple[Group, list[Member]]],
    *,
    owner_id: uuid.UUID,
) -> PublicGroupsView:
    """Group payloads, built off the event loop - see `_build_member_views`.

    A group description is markdown and gets the same full CommonMark resolve
    pass a bio does, and each roster entry decrypts a name to sort and to show,
    so a system with many groups pays the roster's CPU cost several times over.
    Only column attributes of the already-loaded group and member rows are read
    here; the membership rows were resolved into `roster` before the hop.
    """
    out: list[PublicGroupView] = []
    for group, shown in pairs:
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


async def project_groups(
    db: AsyncSession, view: ShareView, *, owner_id: uuid.UUID
) -> PublicGroupsView:
    """Published groups, each with the part of its roster this view shows."""
    pairs = await _projectable_group_rosters(db, view)
    if not pairs:
        return PublicGroupsView(groups=[])
    return await asyncio.to_thread(_build_group_views, pairs, owner_id=owner_id)


def _build_system_view(
    system: System,
    *,
    member_count: int | None,
    member_permalinks: bool,
    expose_system_id: bool,
) -> PublicSystemView:
    """The system card, built off the event loop - see `_build_member_views`.

    One description here rather than a roster's worth, but it is the same
    unbounded markdown parse over an owner-supplied string, and it is on the
    first request of every page load.
    """
    return PublicSystemView(
        id=str(system.id) if expose_system_id else None,
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
        member_permalinks=member_permalinks,
    )


async def project_system(
    db: AsyncSession,
    view: ShareView,
    system: System,
    *,
    expose_system_id: bool,
) -> PublicSystemView:
    """The system header every public page is topped with.

    `expose_system_id` has no default on purpose: every caller has to decide,
    because getting it wrong is the difference between an unlisted link and a
    listed one. A share link is sold to the owner as "an opaque token instead
    of your system id", and the id was in the payload anyway - so two links
    handed to two different people, or one link and the owner's public profile,
    could be tied to the same system by anyone who read the JSON, which is the
    one thing the opaque token exists to prevent. The public-grant routes pass
    True because the id is already in the URL the visitor typed; the link
    routes pass False and the key is served as null.

    Null rather than dropping the key: the key set of this payload is the
    fail-closed contract (see the module docstring on the schemas), and a
    payload whose shape depends on which grant served it would make that
    contract two contracts.
    """
    # A roster this view refuses to serve must not be countable either:
    # "23 members you cannot see" is still a fact about the system, and it is
    # exactly the fact somebody turning the roster off was trying not to
    # publish. Null, not zero - zero would be a claim, and a false one.
    member_count = (
        await _active_member_count(db, view) if view.include_members else None
    )
    return await asyncio.to_thread(
        _build_system_view,
        system,
        member_count=member_count,
        member_permalinks=view.member_permalinks,
        expose_system_id=expose_system_id,
    )


def _build_fronting_view(
    members_by_id: dict[uuid.UUID, Member],
    since_by_member: dict[uuid.UUID, object],
    in_view: set[uuid.UUID],
    *,
    owner_id: uuid.UUID,
    show_count: bool,
) -> PublicFrontingView:
    """Fronting cards, built off the event loop - see `_build_member_views`.

    No bios on this surface, but every named member still decrypts a name, and
    this is the endpoint a page polls on a timer, so it is the one most likely
    to be running many times over at once.
    """
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
                        member.avatar_url, owner_id
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
        hidden_count=hidden if show_count else 0,
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

    return await asyncio.to_thread(
        _build_fronting_view,
        members_by_id,
        since_by_member,
        in_view,
        owner_id=system.user_id,
        show_count=view.fronting_show_count,
    )
