"""Append-only security/auth event log.

Records the auth funnel - logins (success + every failure reason),
registrations, password-reset requests/completions, and self-service
password changes - with the originating client IP, so an operator can
answer questions the per-account lockout counter and Prometheus
aggregates cannot:

  - "Is this IP credential-stuffing?" (many distinct accounts failed
    from one IP in a short window)
  - "Show me everything from this IP / subnet"
  - "What happened on this account before the takeover report came in?"

Append-only by design: no UPDATE/DELETE endpoint. Rows age out via the
`cleanup_security_events` job (see `security_event_retention_days`), so
this is bounded PII retention, not an unbounded archive - IP is personal
data and a short window does the minimisation work.

IP is stored as Postgres INET (NOT the `String(45)` used by
`User.signup_ip` / `TrustedDevice`). That is a deliberate departure: the
whole point of this table is IP search *including subnet matching*
(`ip <<= '203.0.113.0/24'`), which a string column cannot do natively.
The departure is isolated to this table.

Privacy note: failed logins against a non-existent account record
`user_id = NULL` and never store the attempted email (plaintext or hash)
- the IP and the `user_not_found` outcome are enough to spot scanning
without retaining an identifier we'd then owe under a DSAR.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, UUIDMixin
from sheaf.models.types import InetStr


class SecurityEventType(enum.StrEnum):
    """Auth-funnel event categories. The fine-grained reason lives in
    `outcome` (a free string) so new failure modes don't need a migration."""

    LOGIN = "login"
    REGISTER = "register"
    PASSWORD_RESET_REQUEST = "password_reset_request"
    PASSWORD_RESET_COMPLETE = "password_reset_complete"
    PASSWORD_CHANGE = "password_change"


class SecurityEvent(UUIDMixin, Base):
    __tablename__ = "security_events"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    event_type: Mapped[SecurityEventType] = mapped_column(
        # `name=...` must match the type the migration creates, else
        # SQLAlchemy autoderives `securityeventtype` and INSERT fails.
        Enum(
            SecurityEventType,
            name="security_event_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # Fine-grained result: "success", "password_incorrect",
    # "user_not_found", "totp_invalid", "locked", "account_suspended",
    # "account_banned", "captcha_failed", "totp_required", "sent",
    # "invalid_token", "expired_token". Free string by design.
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)

    # The account involved, when known. NULL for unknown-user login
    # attempts and reset requests for non-existent addresses. SET NULL
    # so account deletion doesn't strand the abuse trail (those rows
    # stop being the user's personal data the moment the link is cut).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Originating client IP as resolved by request.client_ip (trusted-
    # proxy aware). INET so subnet queries work. Nullable: a malformed
    # or absent peer address shouldn't drop the event.
    ip: Mapped[str | None] = mapped_column(InetStr, nullable=True)

    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Small structured extras (route, session handle, client name).
    # Never carries member content or plaintext credentials/emails.
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        # "everything from this IP/subnet", newest first.
        Index("ix_security_events_ip_created", "ip", "created_at"),
        # one account's timeline.
        Index("ix_security_events_user_created", "user_id", "created_at"),
        # the stuffing scan: filter to login failures in a window, then
        # group by IP. event_type + created_at narrows the scan; ip is
        # carried so the group-by can be index-only-ish.
        Index(
            "ix_security_events_type_created", "event_type", "created_at", "ip"
        ),
    )
