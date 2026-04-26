import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class TrustedDevice(UUIDMixin, TimestampMixin, Base):
    """A device the user has marked as trusted, allowing TOTP to be skipped
    on subsequent logins from the same browser within the device's TTL.

    The cookie carries an opaque random token; the server stores only its
    HMAC. A trusted device matches at login time when the cookie's HMAC is
    found here AND the row's user_id matches the user logging in AND the
    row hasn't expired. Bulk-revoked on password change, TOTP disable,
    TOTP re-enrolment, and account deletion.
    """

    __tablename__ = "trusted_devices"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )
    nickname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_used_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )
