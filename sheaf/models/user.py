import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String
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


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # Encrypted at application level — store ciphertext
    email: Mapped[str] = mapped_column(String, nullable=False)
    # Blind index for lookups (SHA-256 of normalised email)
    email_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    # TOTP 2FA — encrypted at application level
    totp_secret: Mapped[str | None] = mapped_column(String, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Recovery codes — encrypted JSON array of hashed codes
    recovery_codes: Mapped[str | None] = mapped_column(String, nullable=True)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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

    # Nullable = use tier default. Set by admin to override (support request).
    member_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tier: Mapped[UserTier] = mapped_column(
        Enum(UserTier, values_callable=lambda e: [m.value for m in e]),
        default=UserTier.SELF_HOSTED,
        nullable=False,
    )

    signup_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    system: Mapped["System"] = relationship(back_populates="user", uselist=False)
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
