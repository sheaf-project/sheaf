"""FCM (Firebase Cloud Messaging) HTTP v1 transport.

Uses the service-account JWT-bearer flow to obtain an OAuth2 access
token (cached in process for ~50 minutes), then POSTs to the per-project
messages:send endpoint once per device token.

References:
- https://firebase.google.com/docs/cloud-messaging/auth-server
- https://firebase.google.com/docs/cloud-messaging/send-message

We deliberately don't pull in `google-auth` for the OAuth2 dance — it's
~30 lines with PyJWT, and avoiding the heavyweight dep keeps the install
smaller for self-hosters.
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

logger = logging.getLogger("sheaf.notifications.fcm")

_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

# Refresh access tokens 10 minutes before they actually expire so a
# delivery in flight doesn't hit a freshly-expired token. Google issues
# 1-hour tokens by default.
_TOKEN_LIFETIME_SECONDS = 3600
_TOKEN_REFRESH_MARGIN_SECONDS = 600


@dataclass(frozen=True, slots=True)
class _ServiceAccount:
    project_id: str
    client_email: str
    private_key: str  # PEM, with literal \n already unescaped


@dataclass(slots=True)
class _CachedToken:
    access_token: str
    expires_at: float  # epoch seconds


_token_cache: _CachedToken | None = None


def _load_service_account() -> _ServiceAccount | None:
    """Resolve service-account creds from settings (path wins over inline).
    Returns None when neither setting is populated."""
    raw: str | None = None
    if settings.fcm_service_account_path:
        try:
            raw = Path(settings.fcm_service_account_path).read_text()
        except OSError as exc:
            logger.error("FCM service account path unreadable: %s", exc)
            return None
    elif settings.fcm_service_account_json:
        raw = settings.fcm_service_account_json

    if not raw:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("FCM service account JSON malformed: %s", exc)
        return None

    project_id = data.get("project_id")
    client_email = data.get("client_email")
    private_key = data.get("private_key")
    if not project_id or not client_email or not private_key:
        logger.error("FCM service account missing required fields")
        return None
    return _ServiceAccount(
        project_id=project_id,
        client_email=client_email,
        private_key=private_key,
    )


async def _fetch_access_token(account: _ServiceAccount) -> str | None:
    """Exchange a signed JWT for an OAuth2 access token via Google's
    token endpoint. Returns None on failure (caller treats as transient)."""
    now = int(time.time())
    payload = {
        "iss": account.client_email,
        "scope": _FCM_SCOPE,
        "aud": _OAUTH_TOKEN_URL,
        "iat": now,
        "exp": now + _TOKEN_LIFETIME_SECONDS,
    }
    try:
        assertion = jwt.encode(payload, account.private_key, algorithm="RS256")
    except Exception as exc:  # noqa: BLE001 - signing failure includes bad key shapes
        logger.error("FCM JWT sign failed: %s", exc)
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _OAUTH_TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("FCM oauth2 token request failed: %s", exc)
        return None

    if resp.status_code != 200:
        logger.warning(
            "FCM oauth2 token request rejected: %d %s",
            resp.status_code,
            resp.text[:200],
        )
        return None
    try:
        token = resp.json().get("access_token")
    except json.JSONDecodeError:
        return None
    if not token:
        return None
    return token


async def _get_access_token(account: _ServiceAccount) -> str | None:
    global _token_cache
    now = time.time()
    if (
        _token_cache is not None
        and _token_cache.expires_at - _TOKEN_REFRESH_MARGIN_SECONDS > now
    ):
        return _token_cache.access_token

    token = await _fetch_access_token(account)
    if token is None:
        return None
    _token_cache = _CachedToken(
        access_token=token, expires_at=now + _TOKEN_LIFETIME_SECONDS
    )
    return token


@dataclass(frozen=True, slots=True)
class FcmSendResult:
    """Per-device delivery outcome.

    `dead=True` indicates the device token is permanently gone (404 with
    UNREGISTERED, or 400 with INVALID_ARGUMENT for the token field) and
    the row should be deleted from push_device_tokens. `transient=True`
    means the dispatcher should retry later. `ok=True` means delivered."""

    ok: bool = False
    dead: bool = False
    transient: bool = False
    error: str | None = None


def _classify_response(status_code: int, body: str) -> FcmSendResult:
    if status_code == 200:
        return FcmSendResult(ok=True)
    # 404 with UNREGISTERED, or NOT_FOUND, both mean the token is gone.
    if status_code == 404:
        return FcmSendResult(dead=True, error=f"FCM 404: {body[:200]}")
    if status_code == 400:
        # Could be INVALID_ARGUMENT pointing at the token (dead) or
        # something else (permanent config error). Treat 400 specifically
        # mentioning the token as dead; otherwise transient so the
        # operator can fix.
        if "INVALID_ARGUMENT" in body and "registration" in body.lower():
            return FcmSendResult(dead=True, error=f"FCM 400: {body[:200]}")
        return FcmSendResult(transient=True, error=f"FCM 400: {body[:200]}")
    if status_code == 403:
        # Auth failure or quota — refresh token next call, transient now.
        return FcmSendResult(transient=True, error=f"FCM 403: {body[:200]}")
    if 500 <= status_code < 600:
        return FcmSendResult(transient=True, error=f"FCM {status_code}: {body[:200]}")
    return FcmSendResult(transient=True, error=f"FCM {status_code}: {body[:200]}")


async def send_to_token(
    *,
    device_token: str,
    title: str,
    body: str,
    event_id: str,
    channel_id: str,
    channel_name: str,
    event_type: str,
) -> FcmSendResult:
    """Send one FCM message to one device token.

    Returns a per-device result the caller (handler) can aggregate. Pure
    data payload — Android clients build the user-visible notification
    locally from these fields, matching the design's "client formats
    title/body" requirement.

    `channel_id` / `channel_name` / `event_type` identify the originating
    Sheaf NotificationChannel so the Android client can route into a
    per-subscription Android NotificationChannel (mute one Sheaf channel
    without muting them all). Clients that don't read these fields are
    unaffected — FCM data dicts ignore unknown keys.
    """
    account = _load_service_account()
    if account is None:
        return FcmSendResult(transient=True, error="FCM not configured")
    token = await _get_access_token(account)
    if token is None:
        return FcmSendResult(transient=True, error="FCM oauth2 token fetch failed")

    url = f"https://fcm.googleapis.com/v1/projects/{account.project_id}/messages:send"
    payload = {
        "message": {
            "token": device_token,
            "data": {
                "title": title,
                "body": body,
                "event_id": event_id,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "event_type": event_type,
            },
            "android": {"priority": "high"},
        }
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
            )
    except httpx.HTTPError as exc:
        return FcmSendResult(transient=True, error=f"FCM transport: {exc}")

    return _classify_response(resp.status_code, resp.text)


def _reset_cache_for_tests() -> None:
    """Test hook: drop the cached access token between cases."""
    global _token_cache
    _token_cache = None
