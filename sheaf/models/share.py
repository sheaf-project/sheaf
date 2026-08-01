"""Share views and grants: the exposure model behind public profiles.

Two separated concepts, so the "what do I expose" decision is made once, in the
abstract, and the "who do I trust" decision is made separately:

- **ShareView** - a named, curated projection. An explicit ALLOWLIST of members
  (`ShareViewMember`) and custom fields (`ShareViewField`), plus per-view flags
  (`include_bio`, `include_fronting`). A member or field that was never
  deliberately added is never projected, so the surface fails closed by
  construction rather than by remembering to set a flag.
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
