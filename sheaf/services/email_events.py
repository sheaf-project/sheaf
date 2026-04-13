"""Shared email event processing for bounce/complaint handling.

Provider-agnostic: SES job and SendGrid webhook both call these
functions to apply the same state transitions.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import blind_index
from sheaf.models.user import EmailDeliveryStatus, User

logger = logging.getLogger("sheaf.email_events")


async def apply_bounce(
    db: AsyncSession, email: str, *, permanent: bool
) -> bool:
    """Apply a bounce to the user row. Returns True if a user was updated.

    permanent=True for hard bounces (SES Permanent, SendGrid bounce/blocked/dropped).
    permanent=False for soft bounces (SES Transient, SendGrid deferred).
    """
    email_hash = blind_index(email)
    result = await db.execute(select(User).where(User.email_hash == email_hash))
    user = result.scalar_one_or_none()
    if user is None:
        logger.info("Bounce for unknown recipient (hash only logged)")
        return False

    now = datetime.now(UTC)
    if permanent:
        user.email_delivery_status = EmailDeliveryStatus.HARD_BOUNCED
        user.email_delivery_status_changed_at = now
        user.email_revalidation_required = True
        logger.info("Hard bounce for user %s — flagged for revalidation", user.id)
    else:
        user.email_soft_bounce_count = (user.email_soft_bounce_count or 0) + 1
        if user.email_delivery_status == EmailDeliveryStatus.OK:
            user.email_delivery_status = EmailDeliveryStatus.SOFT_BOUNCING
            user.email_delivery_status_changed_at = now
        logger.info(
            "Soft bounce for user %s (count=%d)",
            user.id, user.email_soft_bounce_count,
        )
    return True


async def apply_complaint(db: AsyncSession, email: str) -> bool:
    """Apply a complaint to the user row. Returns True if a user was updated."""
    email_hash = blind_index(email)
    result = await db.execute(select(User).where(User.email_hash == email_hash))
    user = result.scalar_one_or_none()
    if user is None:
        logger.info("Complaint for unknown recipient (hash only logged)")
        return False

    user.email_delivery_status = EmailDeliveryStatus.COMPLAINED
    user.email_delivery_status_changed_at = datetime.now(UTC)
    user.email_revalidation_required = True
    logger.info("Complaint for user %s — flagged for revalidation", user.id)
    return True
