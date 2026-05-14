import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class DestinationType(enum.StrEnum):
    """Supported destinations and reserved placeholders for ones whose
    handler hasn't shipped yet (channel creation rejects those with 501).

    `mobile_push` is the single, platform-agnostic mobile destination —
    the channel binds to a Sheaf account at redemption, and the dispatcher
    fans out across every `push_device_tokens` row for that account,
    routing each token to FCM (Android) or APNs (iOS, dev/prod per-token)
    automatically. There is no per-channel platform choice; one channel
    rings every device the recipient signs into. Distinct from `web_push`,
    which is anonymous-capable and tied to a single browser subscription.

    The legacy `fcm` / `apns_dev` / `apns_prod` values are kept in the
    enum so existing audit logs / exports remain interpretable, but
    channel creation refuses them — use `mobile_push` instead. A migration
    flips any existing rows over."""

    WEB_PUSH = "web_push"
    WEBHOOK = "webhook"
    NTFY = "ntfy"
    PUSHOVER = "pushover"
    EMAIL = "email"
    MOBILE_PUSH = "mobile_push"
    DISCORD = "discord"
    # Deprecated, retained for migration / read-back of historical rows.
    # Channel creation rejects these; the migration moves any existing
    # data to MOBILE_PUSH.
    APNS_DEV = "apns_dev"
    APNS_PROD = "apns_prod"
    FCM = "fcm"


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
    # Splits the meaning of `destination_state = DISABLED` between the
    # owner pausing the channel (True) and the recipient unsubscribing
    # (False — also covers legacy rows where the cause is unknown). The
    # recipient-facing label renders differently for each: "Paused by
    # sender" vs "Unsubscribed". Cleared on re-enable.
    paused_by_sender: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
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
