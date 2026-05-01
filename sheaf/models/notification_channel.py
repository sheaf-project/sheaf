import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class DestinationType(enum.StrEnum):
    """v1 supports the first four. The remainder are reserved values that the
    schema accepts, but `POST /v1/.../channels` rejects with 501 until the
    matching handler ships."""

    WEB_PUSH = "web_push"
    WEBHOOK = "webhook"
    NTFY = "ntfy"
    PUSHOVER = "pushover"
    EMAIL = "email"
    APNS = "apns"
    FCM = "fcm"
    DISCORD = "discord"


class DestinationState(enum.StrEnum):
    PENDING_REGISTRATION = "pending_registration"  # push types: awaiting recipient redemption
    ACTIVE = "active"
    DISABLED = "disabled"
    PENDING_VERIFICATION = "pending_verification"  # email-only; reserved
    DECLINED_OR_EXPIRED = "declined_or_expired"  # email-only; reserved


class CofrontRedaction(enum.StrEnum):
    COUNT = "count"
    SOMEONE = "someone"
    SUPPRESS = "suppress"


class PayloadSensitivity(enum.StrEnum):
    FULL = "full"
    MINIMAL = "minimal"
    BARE = "bare"


class NotificationChannel(UUIDMixin, TimestampMixin, Base):
    """A configured destination + filter + trigger + delivery-shaping bundle,
    scoped to one watch token."""

    __tablename__ = "notification_channels"

    watch_token_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("watch_tokens.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    destination_type: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    destination_state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=DestinationState.PENDING_REGISTRATION.value,
        server_default=DestinationState.PENDING_REGISTRATION.value,
    )

    # Forward-compat: only "front_change" in v1.
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="front_change", server_default="front_change"
    )

    # Activation (push types only)
    activation_code_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    activation_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    redeemed_by_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Stable per-channel HMAC of the recipient's management URL token.
    recipient_management_token_hash: Mapped[str | None] = mapped_column(
        String, nullable=True
    )

    # L1 base set
    base_all_members: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    base_include_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Triggers
    trigger_on_start: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    trigger_on_stop: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    trigger_on_cofront_change: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cofront_redaction: Mapped[str] = mapped_column(
        String(8), nullable=False, default="count", server_default="count"
    )

    payload_sensitivity: Mapped[str] = mapped_column(
        String(8), nullable=False, default="full", server_default="full"
    )

    # Delivery shaping
    debounce_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    aggregation_window_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    quiet_hours: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Email-specific (NULL for v1 destinations; reserved for the email branch).
    email_delivery_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    email_monthly_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    email_monthly_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    email_month_anchor: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Webhook secret stored encrypted (HMAC needs cleartext at dispatch).
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)

    # Bookkeeping for debounce checks (last successful delivery).
    last_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    watch_token: Mapped["WatchToken"] = relationship(back_populates="channels")
    group_rules: Mapped[list["NotificationChannelGroupRule"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    member_rules: Mapped[list["NotificationChannelMemberRule"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(
            "ix_notification_channels_active",
            "destination_state",
            postgresql_where="destination_state = 'active'",
        ),
    )
