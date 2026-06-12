"""Webhook endpoints for third-party service callbacks."""

import base64
import json
import logging
import secrets
import time
from urllib.parse import parse_qs

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.observability.metrics import (
    email_provider_events_total,
    webhook_signature_failures_total,
)

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
            webhook_signature_failures_total.labels(endpoint="sendgrid").inc()
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

        # Replay window — reject stale or future-dated timestamps.
        if not _timestamp_within_skew(
            timestamp, settings.sendgrid_webhook_max_skew_seconds
        ):
            logger.warning("Rejected SendGrid webhook: stale or bad timestamp")
            webhook_signature_failures_total.labels(endpoint="sendgrid").inc()
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

        if not _verify_sendgrid_signature(
            settings.sendgrid_webhook_public_key, timestamp, raw_body, signature
        ):
            logger.warning("Rejected SendGrid webhook: bad signature")
            webhook_signature_failures_total.labels(endpoint="sendgrid").inc()
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    else:
        # Legacy shared-secret fallback. Deprecated — configure
        # SENDGRID_WEBHOOK_PUBLIC_KEY and enable the signed webhook.
        token = request.query_params.get("token", "")
        if not secrets.compare_digest(token, settings.sendgrid_webhook_secret):
            webhook_signature_failures_total.labels(endpoint="sendgrid").inc()
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    from sheaf.services.email_events import (
        apply_bounce,
        apply_complaint,
        apply_delivered,
    )

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

        if event_type in (
            "bounce", "blocked", "dropped", "deferred", "spamreport", "delivered",
        ):
            email_provider_events_total.labels(
                provider="sendgrid", event=event_type,
            ).inc()

        try:
            if event_type in ("bounce", "blocked", "dropped"):
                if await apply_bounce(db, email, permanent=True):
                    processed += 1
            elif event_type == "deferred":
                if await apply_bounce(db, email, permanent=False):
                    processed += 1
            elif event_type == "spamreport" and await apply_complaint(db, email):
                processed += 1
            elif event_type == "delivered" and await apply_delivered(db, email):
                # A successful delivery clears transient soft-bounce state,
                # so a greylisted first attempt self-heals once the retry
                # lands. Requires the "Delivered" event enabled in the
                # SendGrid Event Webhook config.
                processed += 1
        except Exception:
            logger.exception("Failed to process SendGrid event: %s", event_type)

    await db.commit()
    logger.info("Processed %d SendGrid events from batch of %d", processed, len(events))
    return {"processed": processed}


# SMTP2GO event -> deliverability action. Pure mapping, unit-tested
# separately from the endpoint. Returns one of "hard_bounce",
# "soft_bounce", "complaint", "delivered", or None (event we don't act
# on: processed / open / click / unsubscribe / resubscribe / reject).
#
# `reject` is deliberately ignored: SMTP2GO emits it when it refuses to
# send to an address that ALREADY hard-bounced/complained/unsubscribed,
# so it carries no new state - the address is already flagged. An
# unclassified bounce defaults to soft, the conservative choice: soft
# bounces don't block until the threshold, so a misclassification can't
# wrongly lock anyone out.
def parse_smtp2go_payload(raw: bytes, content_type: str) -> list[dict]:
    """Parse an SMTP2GO webhook body into a list of event dicts.

    SMTP2GO's output type is operator-configurable: JSON or form-encoded
    (`application/x-www-form-urlencoded`). We accept either, so a webhook
    left on the wrong output type still works instead of silently 400ing
    every event. JSON may be a single object or an array; form-encoded is
    always a single event. Raises ValueError on anything we can't read as
    event dict(s).
    """
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        raise ValueError("empty body")
    # JSON if declared as such, or if the body opens like JSON.
    if "json" in content_type.lower() or text[:1] in ("{", "["):
        data = json.loads(text)  # raises ValueError (JSONDecodeError) on junk
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]
        if isinstance(data, dict):
            return [data]
        raise ValueError("unexpected JSON shape")
    # Form-encoded fallback: one event as key=value pairs. parse_qs keeps
    # blank values and never raises; an empty result means it wasn't form
    # data either.
    parsed = parse_qs(text, keep_blank_values=True)
    if not parsed:
        raise ValueError("not JSON or form-encoded")
    return [{k: v[0] for k, v in parsed.items()}]


def smtp2go_event_action(event_type: str, bounce_kind: str | None) -> str | None:
    if event_type == "delivered":
        return "delivered"
    if event_type == "bounce":
        return "hard_bounce" if bounce_kind == "hard" else "soft_bounce"
    if event_type == "spam":
        return "complaint"
    return None


@router.post("/smtp2go/events", dependencies=[rate_limit(300, 60)])
async def smtp2go_events(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive SMTP2GO webhook callbacks for delivery/bounce/spam events.

    SMTP2GO does not sign payloads (no HMAC), so the endpoint is guarded
    by a shared secret in the URL: configure the SMTP2GO webhook to POST
    to /v1/webhooks/smtp2go/events?token=<secret> with JSON output.
    When no secret is configured the endpoint returns 404. Feeds the
    same deliverability lifecycle (apply_bounce / apply_complaint /
    apply_delivered) as the SES and SendGrid handlers.

    Accepts either webhook output type: JSON (single object or array) or
    form-encoded (a single event). SMTP2GO usually posts one event per
    request.
    """
    if not settings.smtp2go_webhook_secret:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    token = request.query_params.get("token", "")
    if not secrets.compare_digest(token, settings.smtp2go_webhook_secret):
        webhook_signature_failures_total.labels(endpoint="smtp2go").inc()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    from sheaf.services.email_events import (
        apply_bounce,
        apply_complaint,
        apply_delivered,
    )

    raw_body = await request.body()
    try:
        events = parse_smtp2go_payload(
            raw_body, request.headers.get("content-type", "")
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body is not valid JSON or form-encoded data",
        ) from None

    processed = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event")
        # SMTP2GO names the recipient `rcpt` (not `email` like SendGrid).
        email = event.get("rcpt") or event.get("email")
        if not email or not event_type:
            continue

        action = smtp2go_event_action(event_type, event.get("bounce"))
        if action is None:
            continue
        email_provider_events_total.labels(
            provider="smtp2go", event=event_type,
        ).inc()

        try:
            if action == "hard_bounce":
                if await apply_bounce(db, email, permanent=True):
                    processed += 1
            elif action == "soft_bounce":
                if await apply_bounce(db, email, permanent=False):
                    processed += 1
            elif action == "complaint":
                if await apply_complaint(db, email):
                    processed += 1
            elif action == "delivered" and await apply_delivered(db, email):
                processed += 1
        except Exception:
            logger.exception("Failed to process SMTP2GO event: %s", event_type)

    await db.commit()
    logger.info("Processed %d SMTP2GO events from batch of %d", processed, len(events))
    return {"processed": processed}
