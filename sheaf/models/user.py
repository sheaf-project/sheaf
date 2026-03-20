import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class UserTier(enum.StrEnum):
    FREE = "free"
    PLUS = "plus"
    SELF_HOSTED = "self_hosted"


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

    storage_used_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )

    tier: Mapped[UserTier] = mapped_column(
        Enum(UserTier, values_callable=lambda e: [m.value for m in e]),
        default=UserTier.SELF_HOSTED,
        nullable=False,
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    system: Mapped["System"] = relationship(back_populates="user", uselist=False)
