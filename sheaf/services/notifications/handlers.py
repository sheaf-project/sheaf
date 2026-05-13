"""Per-destination delivery handlers.

Each handler returns a `DeliveryResult` describing the outcome:
- `ok=True`             : delivered successfully; mark outbox row done.
- `ok=False, transient` : should be retried with backoff (e.g. 5xx, timeout).
- `ok=False, permanent` : destination is gone; disable the channel.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.crypto import decrypt
from sheaf.models.notification_channel import (
    DestinationType,
    NotificationChannel,
)
from sheaf.services.notifications.payload import RenderedMessage
from sheaf.services.notifications.safe_http import (
    SsrfRejected,
    assert_url_safe,
    safe_client,
)

logger = logging.getLogger("sheaf.notifications")


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    ok: bool
    transient: bool = False
    permanent: bool = False
    error: str | None = None


SUCCESS = DeliveryResult(ok=True)


def transient(error: str) -> DeliveryResult:
    return DeliveryResult(ok=False, transient=True, error=error)


def permanent(error: str) -> DeliveryResult:
    return DeliveryResult(ok=False, permanent=True, error=error)


async def deliver(
    channel: NotificationChannel,
    message: RenderedMessage,
    *,
    event_id: str,
    owner_user_id: uuid.UUID | None = None,
    owner_tier: str | None = None,
    db: AsyncSession | None = None,
) -> DeliveryResult:
    """Dispatch a rendered message via the channel's destination handler.

    `owner_user_id` + `owner_tier` are only used by Pushover for per-user
    monthly cap enforcement on the shared deployment app token. Pass them
    on real dispatcher-driven deliveries; pass None for synthetic test
    sends (which still hit the deployment cap but skip the per-user one).

    `db` is required for mobile-push types (FCM / APNs) which fan out to
    every push_device_tokens row matching the channel's redeemed account.
    Other handlers ignore it.
    """
    dtype = DestinationType(channel.destination_type)
    if dtype == DestinationType.WEB_PUSH:
        return await _deliver_web_push(channel, message)
    if dtype == DestinationType.WEBHOOK:
        return await _deliver_webhook(channel, message, event_id=event_id)
    if dtype == DestinationType.NTFY:
        return await _deliver_ntfy(channel, message)
    if dtype == DestinationType.PUSHOVER:
        return await _deliver_pushover(
            channel, message, owner_user_id=owner_user_id, owner_tier=owner_tier
        )
    if dtype in (
        DestinationType.MOBILE_PUSH,
        # Defensive: any legacy rows that somehow survive the migration
        # collapse-to-mobile_push fall through to the same fan-out.
        DestinationType.FCM,
        DestinationType.APNS_DEV,
        DestinationType.APNS_PROD,
    ):
        return await _deliver_mobile_push(
            channel, message, event_id=event_id, db=db
        )
    return permanent(f"unsupported destination type {dtype}")


# --- web push ---------------------------------------------------------------


async def _deliver_web_push(
    channel: NotificationChannel, message: RenderedMessage
) -> DeliveryResult:
    # Server-side config issues are transient: fixing the env var should
    # let pending deliveries through, not require manual channel re-enable.
    if not settings.vapid_public_key or not settings.vapid_private_key:
        return transient("VAPID keys not configured")

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return transient("pywebpush not installed")

    sub = channel.destination_config or {}
    if "endpoint" not in sub or "keys" not in sub:
        return permanent("missing push subscription endpoint/keys")

    payload = json.dumps({"title": message.title, "body": message.body})

    try:
        # pywebpush is sync; offload to threadpool would be ideal but for v1
        # the call is short and we run a small dispatcher concurrency anyway.
        webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject or "mailto:admin@example.com"},
        )
        return SUCCESS
    except WebPushException as exc:
        resp = getattr(exc, "response", None)
        status_code = getattr(resp, "status_code", None) if resp is not None else None
        if status_code in (404, 410):
            return permanent(f"subscription gone ({status_code})")
        return transient(f"web_push error {status_code or exc}")
    except Exception as exc:  # noqa: BLE001 - unknown library exception surfaces
        return transient(f"web_push unexpected error: {exc}")


# --- webhook ----------------------------------------------------------------


_WEBHOOK_FORMATS = {"json", "discord", "slack", "plaintext"}


def _build_webhook_payload(
    fmt: str, message: RenderedMessage, event_id: str
) -> tuple[str, str]:
    """Render the webhook body + Content-Type for the channel's chosen format.

    `json` (default) is Sheaf's structured schema and supports HMAC.
    `discord` and `slack` are vendor-specific JSON shapes; their incoming-
    webhook endpoints don't validate signatures so HMAC is skipped upstream.
    `plaintext` is `title\\nbody` as text/plain, useful for simple SMS
    gateways and ad-hoc collectors. Plaintext still supports HMAC.
    """
    if fmt == "discord":
        payload: dict = {"content": f"**{message.title}**\n{message.body}"}
        if settings.discord_webhook_username:
            payload["username"] = settings.discord_webhook_username
        # Auto-derive an avatar URL from the configured base URL if the
        # operator hasn't set one explicitly. The frontend ships a 512x512
        # square sheaf-icon.png at the site root; Discord requires PNG/JPEG
        # (no SVG).
        avatar = settings.discord_webhook_avatar_url
        if not avatar and settings.sheaf_base_url:
            avatar = settings.sheaf_base_url.rstrip("/") + "/sheaf-icon.png"
        if avatar:
            payload["avatar_url"] = avatar
        return json.dumps(payload), "application/json"
    if fmt == "slack":
        return (
            json.dumps({"text": f"*{message.title}*\n{message.body}"}),
            "application/json",
        )
    if fmt == "plaintext":
        return f"{message.title}\n{message.body}", "text/plain; charset=utf-8"
    # default: Sheaf JSON
    return (
        json.dumps(
            {"event_id": event_id, "title": message.title, "body": message.body},
            sort_keys=True,
        ),
        "application/json",
    )


async def _deliver_webhook(
    channel: NotificationChannel,
    message: RenderedMessage,
    *,
    event_id: str,
) -> DeliveryResult:
    cfg = channel.destination_config or {}
    url = cfg.get("url")
    if not url:
        return permanent("missing webhook URL")
    fmt = cfg.get("format", "json")
    if fmt not in _WEBHOOK_FORMATS:
        return permanent(f"unknown webhook format {fmt!r}")

    try:
        assert_url_safe(url)
    except SsrfRejected as exc:
        return permanent(f"SSRF rejection: {exc}")

    body, content_type = _build_webhook_payload(fmt, message, event_id)
    headers: dict[str, str] = {
        "Content-Type": content_type,
        "User-Agent": settings.webhook_user_agent,
    }
    # Sheaf JSON + plaintext support HMAC. Discord and Slack incoming-webhook
    # endpoints don't validate signatures, so we skip HMAC headers entirely
    # for those, since unknown headers can trigger preflight errors on some
    # cloud frontends.
    if fmt in ("json", "plaintext") and channel.webhook_secret_encrypted is not None:
        try:
            secret = decrypt(channel.webhook_secret_encrypted)
        except Exception as exc:  # noqa: BLE001
            return permanent(f"webhook secret decryption failed: {exc}")
        timestamp = str(int(time.time()))
        signed = f"{timestamp}.{body}".encode()
        signature = hmac.new(secret.encode(), signed, sha256).hexdigest()
        headers["X-Sheaf-Signature"] = signature
        headers["X-Sheaf-Timestamp"] = timestamp
        headers["X-Sheaf-Event-ID"] = event_id
    elif fmt == "json":
        # No secret: still send the event ID so receivers can dedupe.
        headers["X-Sheaf-Event-ID"] = event_id

    async with safe_client() as client:
        try:
            resp = await client.post(url, content=body, headers=headers)
        except httpx.TimeoutException:
            return transient("webhook timeout")
        except httpx.RequestError as exc:
            return transient(f"webhook network error: {exc}")

    if 200 <= resp.status_code < 300:
        return SUCCESS
    if resp.status_code in (410, 404):
        return permanent(f"webhook gone ({resp.status_code})")
    if 400 <= resp.status_code < 500:
        # Owner-misconfigured: don't retry forever, but don't disable yet;
        # treat as transient and let backoff handle it. Owner can fix.
        return transient(f"webhook 4xx ({resp.status_code})")
    return transient(f"webhook 5xx ({resp.status_code})")


# --- ntfy -------------------------------------------------------------------


async def _deliver_ntfy(
    channel: NotificationChannel, message: RenderedMessage
) -> DeliveryResult:
    cfg = channel.destination_config or {}
    server = cfg.get("server_url")
    topic = cfg.get("topic")
    if not server or not topic:
        return permanent("missing ntfy server_url or topic")

    url = f"{server.rstrip('/')}/{topic}"
    try:
        assert_url_safe(url)
    except SsrfRejected as exc:
        return permanent(f"SSRF rejection: {exc}")

    headers = {
        "Title": message.title,
        "User-Agent": settings.webhook_user_agent,
    }
    auth = cfg.get("auth")
    if auth:
        headers["Authorization"] = auth

    async with safe_client() as client:
        try:
            resp = await client.post(url, content=message.body, headers=headers)
        except httpx.TimeoutException:
            return transient("ntfy timeout")
        except httpx.RequestError as exc:
            return transient(f"ntfy network error: {exc}")

    if 200 <= resp.status_code < 300:
        return SUCCESS
    if 400 <= resp.status_code < 500:
        return transient(f"ntfy 4xx ({resp.status_code})")
    return transient(f"ntfy 5xx ({resp.status_code})")


# --- pushover ---------------------------------------------------------------


async def _deliver_pushover(
    channel: NotificationChannel,
    message: RenderedMessage,
    *,
    owner_user_id: uuid.UUID | None = None,
    owner_tier: str | None = None,
) -> DeliveryResult:
    cfg = channel.destination_config or {}
    user_key = cfg.get("user_key")
    if not user_key:
        return permanent("missing pushover user_key")

    # BYO mode: recipient supplies their own Pushover application token.
    # That bypasses both shared-app caps (deployment-wide and per-user) and
    # the operator's debounce floor — the recipient is on their own quota.
    byo_token = cfg.get("app_token")
    using_shared = not byo_token
    app_token = byo_token or settings.pushover_app_token

    if not app_token:
        # No token at all (BYO empty AND server not configured). Transient
        # so a later config fix lets pending deliveries through without
        # auto-disabling the channel.
        return transient("Pushover app token not configured")

    if using_shared:
        from sheaf.services.notifications.pushover_counter import (
            increment_monthly_count,
            increment_user_monthly_count,
            is_over_cap,
            is_user_over_cap,
        )

        # Per-user cap first: a user who's already over their own allotment
        # shouldn't even be charged against the deployment counter, since
        # their delivery is going to fail anyway.
        if owner_user_id is not None and await is_user_over_cap(
            owner_user_id, owner_tier
        ):
            return transient(
                "your monthly Pushover allotment on this instance is "
                "reached; the operator can raise your tier's cap, or you "
                "can set destination_config.app_token to your own Pushover "
                "application to bypass the shared-app limit"
            )

        if await is_over_cap():
            return transient(
                "shared Pushover app monthly cap reached; "
                "ask the operator or set destination_config.app_token "
                "to your own Pushover app to bypass"
            )

    url = "https://api.pushover.net/1/messages.json"
    data = {
        "token": app_token,
        "user": user_key,
        "title": message.title,
        "message": message.body,
    }

    async with safe_client() as client:
        try:
            resp = await client.post(url, data=data)
        except httpx.TimeoutException:
            return transient("pushover timeout")
        except httpx.RequestError as exc:
            return transient(f"pushover network error: {exc}")

    if 200 <= resp.status_code < 300:
        # Only count toward the caps on successful shared-app deliveries,
        # so retries of a failed call don't burn budget twice.
        if using_shared:
            from sheaf.services.notifications.pushover_counter import (
                increment_monthly_count,
                increment_user_monthly_count,
            )

            await increment_monthly_count()
            if owner_user_id is not None:
                await increment_user_monthly_count(owner_user_id)
        return SUCCESS
    if resp.status_code == 400:
        # Pushover returns 400 for invalid user/app keys: permanent.
        return permanent(f"pushover bad key ({resp.status_code})")
    return transient(f"pushover error ({resp.status_code})")


# --- mobile push (FCM + APNs) ----------------------------------------------


async def _deliver_mobile_push(
    channel: NotificationChannel,
    message: RenderedMessage,
    *,
    event_id: str,
    db: AsyncSession | None,
) -> DeliveryResult:
    """Fan-out one notification to every push_device_tokens row matching
    the channel's redeemed_by_account_id, regardless of platform.

    The channel is platform-agnostic: a single mobile_push channel rings
    every device the recipient has signed into, whether iOS or Android.
    Per-row, we route to FCM (android tokens) or APNs (ios_* tokens,
    sandbox vs prod determined per-token) and call the matching handler.

    Aggregation rule: any-success-is-success. Permanent only when every
    device returned a permanent error AND there was at least one device.
    Zero-device case is success-with-no-effect (the user might just not
    have any registered devices yet — e.g. account exists but the app
    hasn't been installed).

    410 / Unregistered responses delete the dead row in-line so the next
    delivery doesn't re-attempt against it.
    """
    from sqlalchemy import delete, select

    from sheaf.models.push_device_token import PushDeviceToken

    if db is None:
        return transient("mobile push handler called without a db session")

    if channel.redeemed_by_account_id is None:
        # The redemption flow refuses to mark a mobile channel ACTIVE
        # without an account binding, so this is defensive only.
        return permanent("mobile push channel has no redeemed_by_account_id")

    result = await db.execute(
        select(PushDeviceToken).where(
            PushDeviceToken.account_id == channel.redeemed_by_account_id,
            # Recipients can mute individual devices from the Receiving tab
            # without unregistering them; disabled rows skip fan-out.
            PushDeviceToken.enabled.is_(True),
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        # No registered devices for this account — not a delivery
        # failure; the user might still have a web-push channel to the
        # same account or just hasn't installed the app yet.
        logger.info(
            "mobile push: no devices registered for account=%s",
            channel.redeemed_by_account_id,
        )
        return SUCCESS

    from sheaf.services.notifications.apns import send_to_token as apns_send
    from sheaf.services.notifications.fcm import send_to_token as fcm_send

    any_ok = False
    all_dead = True
    last_error: str | None = None
    dead_ids: list[uuid.UUID] = []

    for row in rows:
        if row.platform == "fcm":
            outcome = await fcm_send(
                device_token=row.token,
                title=message.title,
                body=message.body,
                event_id=event_id,
            )
        elif row.platform in ("apns_dev", "apns_prod"):
            outcome = await apns_send(
                platform=row.platform,
                device_token=row.token,
                title=message.title,
                body=message.body,
                event_id=event_id,
            )
        else:
            # Unknown platform on the device row — skip, don't treat as
            # a delivery failure for this channel.
            logger.warning(
                "mobile push: unknown device platform=%s for account=%s",
                row.platform,
                channel.redeemed_by_account_id,
            )
            continue

        if outcome.ok:
            any_ok = True
            all_dead = False
        elif outcome.dead:
            dead_ids.append(row.id)
            last_error = outcome.error
        else:
            all_dead = False
            last_error = outcome.error

    if dead_ids:
        await db.execute(
            delete(PushDeviceToken).where(PushDeviceToken.id.in_(dead_ids))
        )
        await db.commit()

    if any_ok:
        return SUCCESS
    if all_dead:
        # Every remaining device is dead. Channel-level: don't disable
        # the channel — the user might re-register a fresh device. Treat
        # as transient so the dispatcher retries (and finds zero rows
        # next time, which is SUCCESS above).
        return transient(last_error or "all mobile push devices dead")
    return transient(last_error or "mobile push delivery failed")
