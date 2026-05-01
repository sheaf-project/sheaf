"""Reserved for the email destination type. Global cross-owner suppression
list, created in v1's migration so the email branch is purely additive
when it lands later. Not used by v1 code."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base


class EmailSuppression(Base):
    __tablename__ = "email_suppressions"

    address_hash: Mapped[str] = mapped_column(String, primary_key=True)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    suppressed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
