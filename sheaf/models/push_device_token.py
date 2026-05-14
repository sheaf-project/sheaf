"""Mobile push device-token rows.

One row per (account, platform, token). The dispatcher fans out a
mobile-push channel to every row matching the channel's
`redeemed_by_account_id` and platform.

See `notes/mobile-push-architecture.md` (or the design-docs repo) for
the rationale on why mobile push is account-anchored rather than
channel-scoped like web push.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class PushPlatform(enum.StrEnum):
    """Stored in `push_device_token.platform`. APNs is split because
    Apple uses two distinct host endpoints (sandbox vs prod) — see
    `DestinationType` for the same rationale."""

    FCM = "fcm"
    APNS_DEV = "apns_dev"
    APNS_PROD = "apns_prod"


class PushDeviceToken(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "push_device_tokens"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "platform", "token", name="uq_push_device_account_platform_token"
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    token: Mapped[str] = mapped_column(String, nullable=False)

    # Diagnostic fields. install_id lets us treat a fresh token from the
    # same install as a rotation (update in place) rather than a new
    # device — matters because some platforms re-issue tokens on app
    # update or OS-side housekeeping without reinstall.
    install_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # User-visible device name set by the mobile app at registration
    # (e.g. "Sarah's iPhone", "Pixel 7"). Optional — rendering code
    # falls back to a platform-based default when absent.
    label: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Soft mute: if False, dispatcher fan-out skips this row. The row
    # stays registered so the user can re-enable without re-installing.
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
