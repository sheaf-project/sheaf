"""Account activity log writer.

One small helper, mirroring `admin_audit.log_admin_action`: append an
`ActivityEvent` and let the caller commit, so the row lands atomically
with the action it records (and rolls back if that action fails). For
automated/background events (import/export runners) the caller passes
``actor_type=SYSTEM`` and commits in the job's own session.

Keep `detail` to small, non-sensitive metadata - counts, a source name, a
format, a key label. Never member content, never secrets, never IP (the
ops-facing IP trail lives in `security_events`).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.activity_event import (
    ActivityAction,
    ActivityActorType,
    ActivityEvent,
)


async def log_activity(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    action: ActivityAction,
    actor_type: ActivityActorType = ActivityActorType.USER,
    target_label: str | None = None,
    detail: dict | None = None,
) -> ActivityEvent:
    """Append one account-activity row (caller commits)."""
    event = ActivityEvent(
        id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        user_id=user_id,
        actor_type=actor_type,
        action=action,
        target_label=(target_label[:200] if target_label else None),
        detail=detail,
    )
    db.add(event)
    return event
