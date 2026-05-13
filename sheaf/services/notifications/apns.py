"""APNs (Apple Push Notification service) HTTP/2 transport.

ES256-signed JWT auth with the deployment's `.p8` key. JWT cached for
~50 minutes per Apple's recommendation; tokens older than 60 minutes
are rejected by APNs.

Same key authenticates against both the sandbox and production hosts —
the dispatcher routes by the channel's destination_type (apns_dev vs
apns_prod). The bundle_id used as the `apns-topic` header is taken from
APNS_BUNDLE_ID (or APNS_BUNDLE_ID_DEV for apns_dev when set).

References:
- https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns
- https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import jwt

from sheaf.config import settings

logger = logging.getLogger("sheaf.notifications.apns")

_HOST_DEV = "api.sandbox.push.apple.com"
_HOST_PROD = "api.push.apple.com"
_PORT = 443

# APNs accepts JWTs up to ~60 minutes old. Refresh well before then so
# in-flight deliveries don't race with expiry.
_TOKEN_LIFETIME_SECONDS = 3600
_TOKEN_REFRESH_MARGIN_SECONDS = 600


@dataclass(slots=True)
class _CachedJwt:
    token: str
    expires_at: float


_jwt_cache: _CachedJwt | None = None


def _load_p8_key() -> str | None:
    """Return the .p8 private key contents (PEM). None when unconfigured."""
    if settings.apns_p8_path:
        try:
            return Path(settings.apns_p8_path).read_text()
        except OSError as exc:
            logger.error("APNs p8 path unreadable: %s", exc)
            return None
    if settings.apns_p8_key:
        # Inline keys often arrive with literal `\n` sequences from env
        # var encoding; normalise so PyJWT's PEM parser accepts them.
        return settings.apns_p8_key.replace("\\n", "\n")
    return None


def _build_jwt() -> str | None:
    if not settings.apns_team_id or not settings.apns_key_id:
        return None
    private_key = _load_p8_key()
    if private_key is None:
        return None
    now = int(time.time())
    try:
        return jwt.encode(
            {"iss": settings.apns_team_id, "iat": now},
            private_key,
            algorithm="ES256",
            headers={"kid": settings.apns_key_id, "alg": "ES256"},
        )
    except Exception as exc:  # noqa: BLE001 - bad key formats raise wide
        logger.error("APNs JWT sign failed: %s", exc)
        return None


def _get_jwt() -> str | None:
    global _jwt_cache
    now = time.time()
    if (
        _jwt_cache is not None
        and _jwt_cache.expires_at - _TOKEN_REFRESH_MARGIN_SECONDS > now
    ):
        return _jwt_cache.token
    token = _build_jwt()
    if token is None:
        return None
    _jwt_cache = _CachedJwt(token=token, expires_at=now + _TOKEN_LIFETIME_SECONDS)
    return token


def _topic_for(platform: str) -> str:
    """The apns-topic header value (an iOS bundle id). Falls back from
    APNS_BUNDLE_ID_DEV to APNS_BUNDLE_ID for apns_dev devices."""
    if platform == "apns_dev" and settings.apns_bundle_id_dev:
        return settings.apns_bundle_id_dev
    return settings.apns_bundle_id


@dataclass(frozen=True, slots=True)
class ApnsSendResult:
    """Per-device delivery outcome. `dead` = token is unregistered and
    the push_device_tokens row should be deleted; `transient` = retry."""

    ok: bool = False
    dead: bool = False
    transient: bool = False
    error: str | None = None


def _classify_response(status_code: int, body: str) -> ApnsSendResult:
    if status_code == 200:
        return ApnsSendResult(ok=True)
    if status_code == 410:
        # BadDeviceToken / Unregistered — token is gone for good.
        return ApnsSendResult(dead=True, error=f"APNs 410: {body[:200]}")
    if status_code == 400:
        # 400s are usually permanent (BadDeviceToken, BadCertificate, etc).
        # We err on the side of dead for 400 so a known-bad token doesn't
        # keep getting retried indefinitely.
        try:
            reason = json.loads(body).get("reason", "")
        except (json.JSONDecodeError, ValueError):
            reason = ""
        if reason in {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}:
            return ApnsSendResult(dead=True, error=f"APNs 400 {reason}")
        return ApnsSendResult(transient=True, error=f"APNs 400: {body[:200]}")
    if status_code == 403:
        # ExpiredProviderToken / InvalidProviderToken — refresh JWT next
        # call. Keep as transient; the cache reset is implicit because
        # the cached token's expires_at is in the future from our POV.
        global _jwt_cache
        _jwt_cache = None
        return ApnsSendResult(transient=True, error=f"APNs 403: {body[:200]}")
    if 500 <= status_code < 600:
        return ApnsSendResult(transient=True, error=f"APNs {status_code}: {body[:200]}")
    return ApnsSendResult(transient=True, error=f"APNs {status_code}: {body[:200]}")


def _build_payload(
    title: str,
    body: str,
    event_id: str,
    channel_id: str,
    channel_name: str,
    event_type: str,
) -> dict:
    """Mutable-content alert + custom keys, per the design doc.

    iOS clients are expected to ship a Notification Service Extension
    that reads `data.title` / `data.body` (and any future custom fields)
    and rewrites the user-visible alert. The placeholder in `aps.alert`
    is what shows if the NSE is missing or times out — keep it neutral.

    `channel_id` / `channel_name` / `event_type` mirror the FCM payload
    so the iOS client can drive thread-id / interruption-level / sound
    overrides per-subscription rather than collapsing everything into
    one bucket.
    """
    return {
        "aps": {
            "alert": {"title": title, "body": body},
            "mutable-content": 1,
            "thread-id": channel_id,
        },
        "data": {
            "title": title,
            "body": body,
            "event_id": event_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "event_type": event_type,
        },
    }


async def send_to_token(
    *,
    platform: str,
    device_token: str,
    title: str,
    body: str,
    event_id: str,
    channel_id: str,
    channel_name: str,
    event_type: str,
) -> ApnsSendResult:
    """Send one APNs message to one device token. Platform must be
    `apns_dev` or `apns_prod` and selects the host."""
    if platform not in ("apns_dev", "apns_prod"):
        return ApnsSendResult(transient=True, error=f"unsupported platform {platform}")

    topic = _topic_for(platform)
    if not topic:
        return ApnsSendResult(transient=True, error="APNs bundle id not configured")

    token = _get_jwt()
    if token is None:
        return ApnsSendResult(transient=True, error="APNs JWT build failed")

    host = _HOST_DEV if platform == "apns_dev" else _HOST_PROD
    url = f"https://{host}:{_PORT}/3/device/{device_token}"
    payload = _build_payload(
        title, body, event_id, channel_id, channel_name, event_type
    )
    try:
        async with httpx.AsyncClient(http2=True, timeout=15.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "authorization": f"bearer {token}",
                    "apns-push-type": "alert",
                    "apns-topic": topic,
                    "apns-id": event_id,
                },
            )
    except httpx.HTTPError as exc:
        return ApnsSendResult(transient=True, error=f"APNs transport: {exc}")

    return _classify_response(resp.status_code, resp.text)


def _reset_cache_for_tests() -> None:
    global _jwt_cache
    _jwt_cache = None
