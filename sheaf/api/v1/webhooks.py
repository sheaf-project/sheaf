"""Webhook endpoints for third-party service callbacks."""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.database import get_db

logger = logging.getLogger("sheaf.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/sendgrid/events")
async def sendgrid_events(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive SendGrid Event Webhook callbacks for bounces and complaints.

    Configure SendGrid to POST to:
        https://<base>/v1/webhooks/sendgrid/events?token=<SENDGRID_WEBHOOK_SECRET>
    """
    if not settings.sendgrid_webhook_secret:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    token = request.query_params.get("token", "")
    if not secrets.compare_digest(token, settings.sendgrid_webhook_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    from sheaf.services.email_events import apply_bounce, apply_complaint

    events = await request.json()
    if not isinstance(events, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expected a JSON array of events",
        )

    processed = 0
    for event in events:
        event_type = event.get("event")
        email = event.get("email")
        if not email or not event_type:
            continue

        try:
            if event_type in ("bounce", "blocked", "dropped"):
                if await apply_bounce(db, email, permanent=True):
                    processed += 1
            elif event_type == "deferred":
                if await apply_bounce(db, email, permanent=False):
                    processed += 1
            elif event_type == "spamreport" and await apply_complaint(db, email):
                processed += 1
        except Exception:
            logger.exception("Failed to process SendGrid event: %s", event_type)

    await db.commit()
    logger.info("Processed %d SendGrid events from batch of %d", processed, len(events))
    return {"processed": processed}
