"""Webhook endpoints for third-party service callbacks."""

import base64
import logging
import secrets
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit

logger = logging.getLogger("sheaf.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# SendGrid Signed Event Webhook header names.
_SIG_HEADER = "X-Twilio-Email-Event-Webhook-Signature"
_TS_HEADER = "X-Twilio-Email-Event-Webhook-Timestamp"


def _timestamp_within_skew(timestamp: str, max_skew_seconds: int) -> bool:
    """True if the webhook timestamp is fresh enough not to be a replay.

    Rejects both stale and future-dated timestamps, and a non-numeric
    header value.
    """
    try:
        skew = abs(time.time() - float(timestamp))
    except ValueError:
        return False
    return skew <= max_skew_seconds


def _verify_sendgrid_signature(
    public_key_b64: str, timestamp: str, body: bytes, signature_b64: str
) -> bool:
    """Verify SendGrid's Signed Event Webhook signature.

    SendGrid signs `timestamp + raw_body` with ECDSA/SHA-256. The header
    carries a base64 DER signature; the verification key is a base64 DER
    SubjectPublicKeyInfo (EC P-256). The key is public — it can only
    verify, never sign — so it lives in plain config.
    """
    try:
        public_key = load_der_public_key(base64.b64decode(public_key_b64))
        signature = base64.b64decode(signature_b64)
    except Exception:
        logger.exception("Malformed SendGrid webhook key or signature")
        return False

    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        logger.error("SENDGRID_WEBHOOK_PUBLIC_KEY is not an EC public key")
        return False

    signed_payload = timestamp.encode() + body
    try:
        public_key.verify(signature, signed_payload, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


@router.post("/sendgrid/events", dependencies=[rate_limit(300, 60)])
async def sendgrid_events(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive SendGrid Event Webhook callbacks for bounces and complaints.

    Authentication: when `sendgrid_webhook_public_key` is configured the
    request must carry a valid Signed Event Webhook signature with a fresh
    timestamp (replay defence). Otherwise it falls back to the legacy
    query-string shared-secret token — deprecated; enable signing instead.
    """
    if (
        not settings.sendgrid_webhook_public_key
        and not settings.sendgrid_webhook_secret
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # The signature covers the exact bytes SendGrid sent — read the raw
    # body and never re-serialize parsed JSON for verification.
    raw_body = await request.body()

    if settings.sendgrid_webhook_public_key:
        signature = request.headers.get(_SIG_HEADER, "")
        timestamp = request.headers.get(_TS_HEADER, "")
        if not signature or not timestamp:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

        # Replay window — reject stale or future-dated timestamps.
        if not _timestamp_within_skew(
            timestamp, settings.sendgrid_webhook_max_skew_seconds
        ):
            logger.warning("Rejected SendGrid webhook: stale or bad timestamp")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

        if not _verify_sendgrid_signature(
            settings.sendgrid_webhook_public_key, timestamp, raw_body, signature
        ):
            logger.warning("Rejected SendGrid webhook: bad signature")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    else:
        # Legacy shared-secret fallback. Deprecated — configure
        # SENDGRID_WEBHOOK_PUBLIC_KEY and enable the signed webhook.
        token = request.query_params.get("token", "")
        if not secrets.compare_digest(token, settings.sendgrid_webhook_secret):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    from sheaf.services.email_events import apply_bounce, apply_complaint

    try:
        events = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body is not valid JSON",
        ) from None
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
