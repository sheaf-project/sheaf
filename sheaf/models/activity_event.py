"""Per-account activity log.

A user-facing record of consequential and automated state changes on a
person's own account / system, so nothing happens silently. This is the
third leg alongside the two existing logs and deliberately distinct from
both:

  - `security_events` is the auth funnel with IP, *operator*-facing and
    bounded-retention (abuse investigation), not surfaced to the user.
  - `admin_audit_events` records *admin* actions on a user (already shown
    to the user on their account-activity page).
  - this log records the user's *own* significant actions (password /
    email / 2FA / API-key / session changes, data-export requests, account
    deletion) and *automated* system actions that touch their data
    (import completed, export ready, and - per the retention incident -
    future background cleanups). It is Safety-independent: PendingAction
    is the System Safety grace machinery; this just records what happened.

Append-only; no UPDATE/DELETE endpoint. Rows age out via the
`cleanup_activity_events` job (`activity_event_retention_days`) so growth
stays bounded. It carries no member content and no secrets - only action
metadata and small structured `detail` (counts, a key name, a format).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, UUIDMixin


class ActivityActorType(enum.StrEnum):
    """Who triggered the event - drives how the UI phrases it ("You ..."
    vs "Your ...")."""

    USER = "user"
    SYSTEM = "system"


class ActivityAction(enum.StrEnum):
    """The consequential action set. New values are additive (no data
    migration needed for a new member). Kept curated rather than
    one-per-CRUD: this is a trail of things worth noticing, not a mirror
    of every edit the user already sees in the UI."""

    # Account / security (actor = user)
    PASSWORD_CHANGED = "password_changed"
    EMAIL_CHANGE_REQUESTED = "email_change_requested"
    EMAIL_CHANGED = "email_changed"
    TOTP_ENABLED = "totp_enabled"
    TOTP_DISABLED = "totp_disabled"
    RECOVERY_CODES_REGENERATED = "recovery_codes_regenerated"
    API_KEY_CREATED = "api_key_created"
    API_KEY_REVOKED = "api_key_revoked"
    SESSION_REVOKED = "session_revoked"
    TRUSTED_DEVICE_REVOKED = "trusted_device_revoked"
    ACCOUNT_DELETION_SCHEDULED = "account_deletion_scheduled"
    ACCOUNT_DELETION_CANCELLED = "account_deletion_cancelled"
    DATA_EXPORT_REQUESTED = "data_export_requested"
    # Automated / system (actor = system)
    IMPORT_COMPLETED = "import_completed"
    EXPORT_READY = "export_ready"


class ActivityEvent(UUIDMixin, Base):
    __tablename__ = "activity_events"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # The account this event belongs to. CASCADE: it is the user's own
    # record and should not outlive the account.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    actor_type: Mapped[ActivityActorType] = mapped_column(
        Enum(
            ActivityActorType,
            name="activity_actor_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    action: Mapped[ActivityAction] = mapped_column(
        Enum(
            ActivityAction,
            name="activity_action",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # Optional short human label for the target (a key name, a format, an
    # import source). Never member content.
    target_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Small structured extras (counts, source, format). No content, no
    # secrets, no IP (that lives in security_events for the ops surface).
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        # The only query: one account's activity, newest first.
        Index("ix_activity_events_user_created", "user_id", "created_at"),
    )
