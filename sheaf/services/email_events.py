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
        logger.info("Hard bounce for user %s - flagged for revalidation", user.id)
    else:
        # Soft bounces are transient and routinely false positives (a
        # greylist trips one on the first attempt, then the retry
        # delivers). One must not block mail. Only after
        # `email_soft_bounce_threshold` accumulate WITHOUT an intervening
        # delivery (apply_delivered resets the count) do we treat the
        # address as undeliverable and flag it for revalidation.
        from sheaf.config import settings

        user.email_soft_bounce_count = (user.email_soft_bounce_count or 0) + 1
        if (
            user.email_delivery_status == EmailDeliveryStatus.OK
            and user.email_soft_bounce_count >= settings.email_soft_bounce_threshold
        ):
            user.email_delivery_status = EmailDeliveryStatus.SOFT_BOUNCING
            user.email_delivery_status_changed_at = now
            user.email_revalidation_required = True
            logger.info(
                "Soft bounce threshold reached for user %s (count=%d) - "
                "flagged undeliverable",
                user.id, user.email_soft_bounce_count,
            )
        else:
            logger.info(
                "Soft bounce for user %s (count=%d, threshold=%d)",
                user.id, user.email_soft_bounce_count,
                settings.email_soft_bounce_threshold,
            )
    return True


async def apply_delivered(db: AsyncSession, email: str) -> bool:
    """Apply a successful-delivery event. Returns True if a user row changed.

    A delivery proves the address is reachable right now, so it clears
    transient soft-bounce state: SOFT_BOUNCING -> OK and the soft-bounce
    counter back to zero. This is what makes greylist-induced soft
    bounces self-heal once the retry lands.

    It deliberately does NOT clear HARD_BOUNCED or COMPLAINED. A hard
    bounce means the mailbox was rejected outright, and a complaint is
    an explicit "stop emailing me" signal from the recipient; neither
    should be silently undone by a later delivery to (e.g.) a different
    message still in flight. Those clear only when the user re-verifies.
    """
    email_hash = blind_index(email)
    result = await db.execute(select(User).where(User.email_hash == email_hash))
    user = result.scalar_one_or_none()
    if user is None:
        logger.info("Delivery for unknown recipient (hash only logged)")
        return False

    changed = False
    if user.email_delivery_status == EmailDeliveryStatus.SOFT_BOUNCING:
        user.email_delivery_status = EmailDeliveryStatus.OK
        user.email_delivery_status_changed_at = datetime.now(UTC)
        user.email_revalidation_required = False
        changed = True
    if user.email_soft_bounce_count:
        user.email_soft_bounce_count = 0
        changed = True
    if changed:
        logger.info("Delivery cleared soft-bounce state for user %s", user.id)
    return changed


def clear_delivery_state(user: User) -> None:
    """Reset a user's deliverability flags to healthy.

    Called when the user proves control + reachability of an address by
    completing email verification (re-verify of a flagged address, or
    verifying a freshly changed one). This is the user-facing escape
    hatch from a HARD_BOUNCED / COMPLAINED / soft-threshold block - the
    only thing that clears those states, since a provider `delivered`
    event deliberately does not (see apply_delivered).
    """
    user.email_delivery_status = EmailDeliveryStatus.OK
    user.email_delivery_status_changed_at = datetime.now(UTC)
    user.email_soft_bounce_count = 0
    user.email_revalidation_required = False


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
