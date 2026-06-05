"""Helper for writing admin audit-log rows.

Every admin endpoint that mutates state (or performs an unusual
privacy-sensitive read) calls `log_admin_action` to append a row.
The caller commits — this keeps the write atomic with the action's
own transaction, so a rollback of the action also rolls back its
audit row instead of leaving a phantom "we did X" entry.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import decrypt
from sheaf.models.admin_audit_event import (
    AdminAuditAction,
    AdminAuditEvent,
    AdminAuditTargetType,
)
from sheaf.models.user import User


def _safe_decrypt_email(user: User) -> str | None:
    """Decrypt the admin's email for the audit row.

    A decrypt failure must NOT abort the action being logged — admin
    audit-write is a side-effect of the action, not a precondition.
    Falls back to None so the row is still written and remains useful
    via admin_user_id + before_json attribution.
    """
    try:
        return decrypt(user.email)
    except Exception:
        return None


async def log_admin_action(
    db: AsyncSession,
    *,
    admin: User,
    action: AdminAuditAction,
    target_type: AdminAuditTargetType,
    target_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AdminAuditEvent:
    """Append an admin audit-log row. Caller commits.

    `before` and `after` should be small dicts of human-readable
    fields, not whole-row dumps — the table is browse-friendly and
    big JSONs hurt readability. For USER_UPDATE pass only the keys
    that actually changed; the helper does no diff itself.
    """
    event = AdminAuditEvent(
        id=uuid.uuid4(),
        admin_user_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_user_id=target_user_id,
        reason=reason,
        before_json=before,
        after_json=after,
        created_at=datetime.now(UTC),
        admin_email=_safe_decrypt_email(admin),
    )
    db.add(event)
    return event
