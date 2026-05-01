"""Activation + management token helpers for notification channels.

Activation codes are issued per push-style channel (web_push for v1; email
when it lands). They are single-use, ~7-day TTL, and stored as keyed HMACs.
The cleartext code only exists in the response sent to the owner once, who
relays it to the recipient out-of-band.

Management tokens are stable per channel, issued at redemption, and let
the recipient view/disable their subscription without an account.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sheaf.config import settings

# Domain-separation labels so the same JWT secret can key several distinct
# token namespaces without collisions.
_ACTIVATION_LABEL = b"sheaf-channel-activation-v1"
_MANAGEMENT_LABEL = b"sheaf-channel-management-v1"


def _hmac_with_label(label: bytes, token: str) -> str:
    key = hmac.new(label, settings.jwt_secret_key.encode(), hashlib.sha256).digest()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def hash_activation_code(code: str) -> str:
    return _hmac_with_label(_ACTIVATION_LABEL, code)


def hash_management_token(token: str) -> str:
    return _hmac_with_label(_MANAGEMENT_LABEL, token)


@dataclass(frozen=True, slots=True)
class IssuedActivation:
    code: str
    code_hash: str
    expires_at: datetime


def issue_activation_code(ttl_days: int = 7) -> IssuedActivation:
    """Generate a fresh activation code + hash + expiry.

    Caller writes `code_hash` and `expires_at` to the channel row; `code` is
    embedded in the activation URL returned to the owner exactly once.
    """
    code = secrets.token_urlsafe(32)
    return IssuedActivation(
        code=code,
        code_hash=hash_activation_code(code),
        expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
    )


@dataclass(frozen=True, slots=True)
class IssuedManagementToken:
    token: str
    token_hash: str


def issue_management_token() -> IssuedManagementToken:
    token = secrets.token_urlsafe(32)
    return IssuedManagementToken(
        token=token,
        token_hash=hash_management_token(token),
    )


def activation_code_matches(code: str, stored_hash: str) -> bool:
    """Constant-time compare of a redeemed code against its stored hash."""
    return hmac.compare_digest(hash_activation_code(code), stored_hash)


def management_token_matches(token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_management_token(token), stored_hash)


def activation_url(base_url: str, channel_id: uuid.UUID, code: str) -> str:
    """Compose the recipient-facing activation URL.

    Format: {base_url}/notifications/redeem?code=...&channel={uuid}

    `base_url` typically comes from settings (frontend origin). The channel
    ID is included so the recipient page can pre-populate context without
    a separate lookup; the code is what actually authenticates.
    """
    base = base_url.rstrip("/")
    return f"{base}/notifications/redeem?code={code}&channel={channel_id}"


def management_url(base_url: str, token: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/notifications/manage/{token}"
