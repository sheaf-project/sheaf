"""Per-front-entry audit log.

Append-only. Each row captures one explicit edit of a front entry —
actor, when, the full pre-edit snapshot, and the full post-edit
snapshot. Walking the rows in created_at order lets you reconstruct
exactly what the entry looked like at any point.

Hard-deleted with the front entry itself via FK cascade — the audit
log is bound to the entry, not to the system. If the entry is purged
(retention, manual delete), its history goes with it.

Auto-end on `replace_fronts=true` and other system-driven mutations
do NOT write audit rows; only explicit PATCH /v1/fronts/{id} calls do.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, UUIDMixin


class FrontAuditEvent(UUIDMixin, Base):
    __tablename__ = "front_audit_events"

    front_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fronts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Nullable so a user account deletion (SET NULL) doesn't take audit
    # rows with it — the historical record stays, attribution just goes
    # blank. We don't have per-member auth, so attribution is account-level.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Snapshot of the system's currently-fronting set at the moment of
    # the edit. Same forensic shape as polls' fronting snapshot — if you
    # later want to know "who was at front when Alice rewrote this old
    # entry?", this is the answer. List of UUID strings.
    fronting_member_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )

    # Full pre-edit and post-edit snapshots. Shape:
    #   {
    #     "started_at": "2026-05-10T12:00:00+00:00",
    #     "ended_at":   "2026-05-10T13:00:00+00:00" | null,
    #     "member_ids": ["uuid1", "uuid2", ...],
    #     "custom_status_encrypted": "<base64 ciphertext>" | null,
    #   }
    # custom_status stays encrypted at rest in the snapshot exactly as
    # it does on the live front; the audit-read endpoint decrypts for
    # the response. Same scope (fronts:read) gates both.
    before_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    after_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
