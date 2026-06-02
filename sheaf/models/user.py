import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class UserTier(enum.StrEnum):
    FREE = "free"
    PLUS = "plus"
    SELF_HOSTED = "self_hosted"


class AccountStatus(enum.StrEnum):
    ACTIVE = "active"
    PENDING_APPROVAL = "pending_approval"
    SUSPENDED = "suspended"
    BANNED = "banned"
    PENDING_DELETION = "pending_deletion"


class EmailDeliveryStatus(enum.StrEnum):
    OK = "ok"
    SOFT_BOUNCING = "soft_bouncing"
    HARD_BOUNCED = "hard_bounced"
    COMPLAINED = "complained"


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # Encrypted at application level — store ciphertext
    email: Mapped[str] = mapped_column(String, nullable=False)
    # Blind index for lookups (keyed HMAC-SHA-256 of normalised email)
    email_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    # TOTP 2FA — encrypted at application level
    totp_secret: Mapped[str | None] = mapped_column(String, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Recovery codes — encrypted JSON array of hashed codes
    recovery_codes: Mapped[str | None] = mapped_column(String, nullable=True)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Per-user override: lets this user upload images even when
    # settings.allow_image_uploads is False. Admins are always allowed.
    can_upload_images: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    # Per-user override for animated avatars (GIF / animated WebP). Combined
    # with the settings.allow_animated_uploads master switch and the
    # tier-based eligibility set in sheaf.files.animation_allowed.
    can_upload_animated_images: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    account_status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, values_callable=lambda e: [m.value for m in e]),
        default=AccountStatus.ACTIVE,
        nullable=False,
        server_default="active",
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    email_verification_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    password_reset_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_reset_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Nullable = use tier default. Set by admin to override (support request).
    member_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tier: Mapped[UserTier] = mapped_column(
        Enum(UserTier, values_callable=lambda e: [m.value for m in e]),
        default=UserTier.SELF_HOSTED,
        nullable=False,
    )

    signup_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    invite_code_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invite_codes.id", ondelete="SET NULL"), nullable=True
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Per-account brute-force lockout. failed_login_count accumulates across
    # wrong-password and wrong-TOTP attempts; locked_until gates the login
    # endpoint until the lockout window elapses. Both reset on a successful
    # login, and the count is reset on the next attempt after locked_until
    # passes so a returning user isn't immediately re-locked on one typo.
    failed_login_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Account deletion
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deletion_reminders_sent: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # Newsletter / marketing mail consent. Explicit opt-in; timestamp records
    # when consent was given for audit/GDPR purposes.
    newsletter_opt_in: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    newsletter_opted_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Email deliverability state — driven by SES bounce/complaint notifications.
    # When not OK, no further mail is sent to this address until it's resolved
    # (user updates email + re-verifies, or admin clears).
    email_delivery_status: Mapped[EmailDeliveryStatus] = mapped_column(
        Enum(EmailDeliveryStatus, values_callable=lambda e: [m.value for m in e]),
        default=EmailDeliveryStatus.OK,
        nullable=False,
        server_default="ok",
    )
    email_delivery_status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    email_soft_bounce_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    # Set on hard bounce or complaint — forces user to re-enter and re-verify
    # their email address on next login before they can use the app normally.
    email_revalidation_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    # Shield-mode opt-out. When the operator engages cf-shield (Cloudflare
    # under-attack + revoked direct-origin SG ingress), every login on the
    # SaaS necessarily routes through the CDN. Users who explicitly do not
    # want their traffic proxied by Cloudflare set this flag; on the up
    # edge their sessions are invalidated so they cannot unwittingly
    # traverse the CDN. They are bounced to login (also CDN-fronted) and
    # will not be able to authenticate again until shield mode clears.
    # The flag has no effect on instances that don't have a Cloudflare
    # break-glass setup (settings.shield_mode_enabled=false), but the
    # column always exists so selfhosters don't need a conditional
    # migration.
    disable_cdn_during_ddos: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    # Relationships
    system: Mapped["System"] = relationship(back_populates="user", uselist=False)
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
