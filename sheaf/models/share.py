"""Share views and grants: the exposure model behind public profiles.

Two separated concepts, so the "what do I expose" decision is made once, in the
abstract, and the "who do I trust" decision is made separately:

- **ShareView** - a named, curated projection. An explicit ALLOWLIST of members
  (`ShareViewMember`) and custom fields (`ShareViewField`), plus per-view flags
  (`include_bio`, `include_fronting`). A member or field that was never
  deliberately added is never projected, so the surface fails closed by
  construction rather than by remembering to set a flag. The single, deliberate
  exception is `include_all_public_members`, which makes the roster track
  `Member.privacy == public` live; see the column's own comment for why that
  particular signal is allowed to be a rule when group membership is not.
- **ShareGrant** - points a subject at a view. Phase 1 ships two subjects:
  `public` (reachable at the system's UUID) and `link` (an opaque, revocable,
  rotatable bearer token). The user-to-user `user` subject is deliberately
  parked; see the design doc.

Nothing is ever readable publicly unless it is BOTH inside a view AND that view
has an active grant. Both halves carry a pending lifecycle so that exposing
something honours the System Safety grace window, while revoking is immediate.

Statuses and `subject_type` are String(16) rather than Postgres ENUMs on
purpose: adding the parked `user` subject when friends lands then needs no type
migration. Mirrors the `PendingAction.status` precedent.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class ShareSubjectType(StrEnum):
    PUBLIC = "public"
    LINK = "link"


class LinkPreviewMode(StrEnum):
    """What a chat client shows when somebody pastes a view's URL.

    `String(16)` on the column rather than a Postgres ENUM, for exactly the
    reason `subject_type` and the statuses here are: a third mode (name and
    avatar but no description text, say) then needs no type migration, only a new
    member here. Same precedent, same reasoning.
    """

    # Names nobody: "a public system profile powered by Sheaf". The default, and
    # what every view that existed before this column reads as.
    GENERIC = "generic"
    # The system's name, avatar and a short snippet of its description.
    SYSTEM_DETAILS = "system_details"


class ShareGrantStatus(StrEnum):
    # Created but not yet live: the System Safety grace window has not elapsed.
    # A pending grant reads exactly like a revoked one from the public surface.
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


class ShareItemStatus(StrEnum):
    """Lifecycle for a member/field's membership of a view.

    Adding to a view that already has a live grant is a loosening, so the row
    lands PENDING and the finalize job promotes it. Removal is immediate.
    """

    PENDING = "pending"
    ACTIVE = "active"


class ShareView(UUIDMixin, TimestampMixin, Base):
    """A named, curated projection of a system."""

    __tablename__ = "share_views"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Whether the member roster is served at all. ON by default (and a
    # server_default of true, so every view that existed before this column did
    # keeps serving exactly the roster it served then). Turning it off does not
    # empty the view's allowlist - the curation is still there, it is simply
    # not published, so switching it back on restores the same roster rather
    # than asking the owner to rebuild it. With it off the members endpoint
    # 404s outright: an empty list would answer "does this profile have a
    # roster?" for anyone who asked.
    include_members: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    # Whether the roster is "every member set to public", evaluated LIVE, rather
    # than only the members explicitly listed in `ShareViewMember`. Off by
    # default, because a new capability never arrives switched on.
    #
    # This is the one place the module docstring's "nothing is exposed
    # implicitly" rule is relaxed, and it is relaxed deliberately and narrowly.
    # A group expansion is still a one-shot picker precisely because group
    # membership is not a publishing decision - people are put in "Littles"
    # for reasons that have nothing to do with strangers. `privacy == public`
    # IS the publishing decision: it is the member's exposure ceiling, the
    # single thing an owner sets to mean "this one may be seen". So a view can
    # honestly track it, and an owner who marks somebody public later gets what
    # they asked for without re-editing the view.
    #
    # It changes WHO is in the view, never WHETHER the view serves a roster:
    # `include_members` stays the on/off switch over the member list, and every
    # surface downstream (bios, relationships, group rosters, the fronting
    # names) applies its own `include_members` gate exactly as it does for
    # explicitly-listed members. With this on, the `ShareViewMember` rows are
    # left untouched but stop deciding anything - every one of them that would
    # have been served is a public member and so is already included - which is
    # what makes turning it back off restore the curated roster intact.
    #
    # `never_shareable` is NOT relaxed: those members are excluded by
    # `share_projection._active_member_filter` on their own predicate, with no
    # override, flag or no flag.
    #
    # Because this is live, raising ANY member to public becomes an act of
    # publishing on its own. `sharing.member_privacy_raise_exposes` is the gate
    # that knows it; see its docstring.
    include_all_public_members: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Whether member bios are included for members in this view. Bios are
    # markdown and go through the usual image-ref resolution on render.
    include_bio: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Live front state. Off by default: real-time presence on a link-shareable
    # URL is the sharpest surface this feature has. Front HISTORY is never
    # exposed by any view.
    include_fronting: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # When a member who is NOT in this view is fronting: True collapses them to
    # an anonymous count, False omits them entirely.
    fronting_show_count: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    # Whether relationships between members in this view are shown. Off by
    # default, and doubly gated: the flag only decides whether the endpoint
    # exists at all, while each individual edge still has to be marked `public`
    # AND have both of its endpoints projected by this view. Turning it on
    # therefore publishes nothing on its own.
    include_relationships: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Whether this view shows the system's public groups. Off by default, and
    # gated twice over like relationships: the flag decides whether the
    # endpoint exists, while each group still has to be marked `public`
    # itself. A published group's roster is the intersection of its members
    # with the members this view already shows, so turning this on can never
    # name somebody the view was not already naming.
    include_groups: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Whether each shown member also gets a stable public URL of their own.
    # Deliberately NOT one of the EXPOSURE_FLAGS above, and deliberately
    # without a pending twin: it publishes no data the roster does not already
    # publish, it only gives that data an address. Both directions are
    # therefore immediate and ungated - staging a change that exposes nothing
    # would only teach people the grace window is theatre. It is still off by
    # default, because a durable link is a different thing to hand out than a
    # row in a list, and that choice should be made rather than inherited.
    member_permalinks: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # What a chat client shows when somebody pastes this view's URL. See
    # `LinkPreviewMode`: `generic` (the default) names nobody, `system_details`
    # adds the system's name, avatar and a short snippet of its description, so
    # the link looks like a profile rather than an anonymous blob. Both are
    # legitimate wants - one hides who the link belongs to, the other makes a
    # deliberately-public page look like the page it is - which is why it is a
    # setting and not a decision, and why it defaults to the quiet one.
    #
    # It IS one of the EXPOSURE_FLAGS, unlike `member_permalinks` above, and the
    # difference is worth being exact about. Permalinks give an address to data
    # the roster already published, so nobody learns anything new. This flag
    # pushes the system's name and picture into a third party's cache, on the
    # strength of one paste, with no reader having chosen to open anything - the
    # cache keeps it whether or not the recipient ever clicks, and keeps it after
    # the profile goes dark. That is strictly more exposure than the page alone,
    # even though every FIELD on the card is a field the page already serves, so
    # turning it on gets the same re-auth and the same grace window as any other
    # way of showing more.
    #
    # Only a `public` grant ever acts on it. A share LINK is a secret in its own
    # right - the opaque token exists so the system behind it is not learnable -
    # so a rich card on a `/s/` URL would hand a chat service exactly what
    # keeping the URL quiet was protecting. The link routes therefore never read
    # this column; see `sheaf/api/link_preview.py`. It still lives on the view
    # rather than the grant because every other display decision does, and
    # because a view commonly backs both a public profile and some links: the
    # setting is the owner's answer for the public one, and the links are
    # unaffected by construction rather than by a second setting to forget.
    link_preview_mode: Mapped[str] = mapped_column(
        String(16),
        default=LinkPreviewMode.GENERIC,
        server_default=LinkPreviewMode.GENERIC.value,
        nullable=False,
    )

    # The same choice for a MEMBER PERMALINK's URL, and a separate column rather
    # than a third rung on the one above, because the two exposures do not contain
    # one another. A system card reveals the system's name, avatar and description
    # snippet; a member card reveals one specific member's name and avatar. Neither
    # is a superset of the other, so an ordered dial would have forced system
    # details on as the price of member cards - a constraint invented by the column
    # rather than by anything about privacy. Wanting a member permalink you
    # deliberately handed someone to unfurl properly while the system-level card
    # stays anonymous is a coherent position, and so is the reverse (the common
    # one).
    #
    # Same shape as its sibling in every other respect: `generic` default, its own
    # pending twin, its own slot in EXPOSURE_FLAGS, so raising it takes the same
    # re-auth and the same grace window.
    #
    # Two gates beyond the mode itself, both enforced in the preview route and
    # neither reimplemented there: the view has to be publishing permalinks at all
    # (`member_permalinks`, or the URL is a 404 and a card for it would be
    # advertising a page that does not exist), and the member has to be one this
    # view actually serves under their own privacy level - decided by asking
    # `share_projection.project_members`, never by a second copy of the visibility
    # rule. A private member inside a public view previews generic.
    member_link_preview_mode: Mapped[str] = mapped_column(
        String(16),
        default=LinkPreviewMode.GENERIC,
        server_default=LinkPreviewMode.GENERIC.value,
        nullable=False,
    )

    # Staged flag flips. Turning one of the nine exposure flags ON while the
    # view is already shared exposes more, so the new value parks here and the
    # finalize sweep copies it onto the live flag once `flags_activate_at`
    # passes - the same PENDING lifecycle the member and field rows carry,
    # expressed as columns because a flag has nowhere else to live. NULL means
    # "nothing staged for this flag". Turning a flag OFF is immediate and
    # clears its pending value: going dark always wins.
    pending_include_bio: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    pending_include_fronting: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    pending_fronting_show_count: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    pending_include_relationships: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    pending_include_members: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    pending_include_groups: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    # The one pending twin that is not a Boolean, because its live column is not
    # either. `promote_view_flags` copies whatever is parked here onto the live
    # column and never inspects the value, so it needed no change for this.
    pending_link_preview_mode: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    pending_member_link_preview_mode: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    pending_include_all_public_members: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    # Shared activation time for whatever is staged above. NULL whenever no
    # pending value is set.
    flags_activate_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    members: Mapped[list["ShareViewMember"]] = relationship(
        back_populates="view", cascade="all, delete-orphan"
    )
    # Provenance only. Never consulted when projecting; see ShareViewGroup.
    groups: Mapped[list["ShareViewGroup"]] = relationship(
        back_populates="view", cascade="all, delete-orphan"
    )
    fields: Mapped[list["ShareViewField"]] = relationship(
        back_populates="view", cascade="all, delete-orphan"
    )
    grants: Mapped[list["ShareGrant"]] = relationship(
        back_populates="view", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("system_id", "name", name="uq_share_views_system_name"),
    )


class ShareViewMember(UUIDMixin, Base):
    """A member deliberately added to a view. Explicit ORM class (not a bare
    association Table) so the export/import parity guard can see it."""

    __tablename__ = "share_view_members"

    view_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("share_views.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Which group expansion created this row. NULL means the owner added this
    # member by hand - or the group has since been deleted, which deliberately
    # degrades to treated-as-manual (see the FK's SET NULL) so deleting a group
    # never silently detaches its members from a view.
    #
    # Attribution only, and only consulted when a group is DETACHED: the row
    # itself is the sole authority on who is exposed, exactly as before, and
    # nothing here is read at projection time. It exists because detaching a
    # group used to remove that group's CURRENT roster from the view, which
    # over-removed - a member the owner had also picked by hand, or one an
    # overlapping group had brought in, got pulled out even though the detached
    # group was not why they were there.
    added_via_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ShareItemStatus.ACTIVE,
        server_default=ShareItemStatus.ACTIVE.value,
    )
    activates_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    view: Mapped["ShareView"] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint("view_id", "member_id", name="uq_share_view_members"),
        Index("ix_share_view_members_due", "status", "activates_at"),
    )


class ShareViewGroup(UUIDMixin, Base):
    """A group a view's membership was populated FROM.

    Deliberately NOT a live rule. `ShareViewMember` is always the sole
    authority on who is exposed; adding a group expands its current members
    into explicit member rows and records the association here for provenance
    ("these came from Littles") and for an explicit, user-initiated re-sync.

    Evaluating group membership at read time would mean that adding someone to
    a group silently publishes them - no deliberate publish step, no grace
    window, which is exactly the accidental-outing failure this whole feature
    is built to prevent. Group membership changes therefore never move anyone
    into or out of a view on their own.

    Detaching one of these rows can offer to take its members with it, and the
    members it means are the ones stamped `ShareViewMember.added_via_group_id`
    - what this expansion actually added - never the group's current roster,
    which is a different set and includes people this group never put here.
    """

    __tablename__ = "share_view_groups"

    view_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("share_views.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Last time this group's members were expanded into the view, so the UI can
    # show "group has changed since you last synced".
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    view: Mapped["ShareView"] = relationship(back_populates="groups")

    __table_args__ = (
        UniqueConstraint("view_id", "group_id", name="uq_share_view_groups"),
    )


class ShareViewField(UUIDMixin, Base):
    """A custom-field definition deliberately exposed by a view."""

    __tablename__ = "share_view_fields"

    view_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("share_views.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_field_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ShareItemStatus.ACTIVE,
        server_default=ShareItemStatus.ACTIVE.value,
    )
    activates_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    view: Mapped["ShareView"] = relationship(back_populates="fields")

    __table_args__ = (
        UniqueConstraint("view_id", "field_id", name="uq_share_view_fields"),
        Index("ix_share_view_fields_due", "status", "activates_at"),
    )


class ShareGrant(UUIDMixin, Base):
    """Points a subject at a view.

    `token_hash` holds a KEYED HMAC of the link token (see
    `sheaf.crypto.hash_share_token`), never the token itself: a DB dump must not
    yield working links. The raw token is returned exactly once, at creation.
    """

    __tablename__ = "share_grants"

    # Denormalised from view.system_id so the audit query and every tenant
    # scope check is a single-table predicate.
    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    view_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("share_views.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # NULL for a `public` grant (which is located by the system's UUID).
    token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )

    # Owner-facing label so a link can be identified in the audit surface
    # without storing any part of the token itself.
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ShareGrantStatus.PENDING,
        server_default=ShareGrantStatus.PENDING.value,
    )
    activates_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    view: Mapped["ShareView"] = relationship(back_populates="grants")

    __table_args__ = (
        # At most one live public grant per system: "public" is a single
        # audience, so two competing public views would be ambiguous.
        Index(
            "uq_share_grants_one_public",
            "system_id",
            unique=True,
            postgresql_where="subject_type = 'public' AND revoked_at IS NULL",
        ),
        Index("ix_share_grants_due", "status", "activates_at"),
    )
