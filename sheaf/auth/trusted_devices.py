"""Trusted-device tokens — 30-day TOTP bypass cookies.

A trusted device is a long-lived cookie a user can opt into at login when
they enter their TOTP code. While the cookie is valid, the same browser
can log in with just email + password and skip the TOTP step. Devices
are listed in settings, can be revoked individually, and are wiped on
password change, TOTP disable, and TOTP re-enrolment.

The cookie carries an opaque random token. The DB stores only an HMAC
of that token (same scheme as mail tokens) so a DB dump alone can't
forge a cookie.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.models.trusted_device import TrustedDevice

TRUSTED_DEVICE_COOKIE = "sheaf_trusted_device"
TRUSTED_DEVICE_TTL_DAYS = 30


def _hash_token(token: str) -> str:
    """Keyed HMAC of a trusted-device token. Same key as mail tokens — the
    JWT secret — so the running app's in-memory key is required to verify
    a cookie against a stored row."""
    key = settings.jwt_secret_key.encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


async def mint_trusted_device(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    user_agent: str,
    ip: str | None,
    nickname: str | None = None,
    client_name: str = "",
) -> tuple[str, TrustedDevice]:
    """Create a new trusted-device row and return (raw_token, row).

    The caller sets the cookie with the raw token; the DB only ever sees
    the HMAC.

    `client_name` is the parsed/friendly client identifier (the same
    string sessions store), supplied by the caller after consulting
    X-Sheaf-Client and falling back to a User-Agent parse. Stored
    verbatim on the row so the trusted-devices list can show a clean
    label without re-parsing on every render.

    `last_used_at` is seeded to the same moment as creation so the row
    reads as "last used: just now" in the UI right after minting,
    rather than blank until the next login.
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=TRUSTED_DEVICE_TTL_DAYS)
    device = TrustedDevice(
        user_id=user_id,
        token_hash=_hash_token(token),
        nickname=nickname,
        user_agent=user_agent[:500],
        client_name=client_name[:64],
        created_ip=ip,
        last_used_at=now,
        last_used_ip=ip,
        expires_at=expires_at,
    )
    db.add(device)
    await db.flush()
    return token, device


async def verify_trusted_device(
    db: AsyncSession,
    token: str | None,
    user_id: uuid.UUID,
    *,
    ip: str | None,
) -> TrustedDevice | None:
    """If `token` is a non-expired trusted device for `user_id`, return the
    row (with last_used_at + last_used_ip touched). Otherwise return None.

    Ties the device to the user so a cookie minted by user A can't bypass
    TOTP on user B's account.
    """
    if not token:
        return None
    token_hash = _hash_token(token)
    result = await db.execute(
        select(TrustedDevice).where(TrustedDevice.token_hash == token_hash),
    )
    device = result.scalar_one_or_none()
    if device is None:
        return None
    if device.user_id != user_id:
        return None
    if device.expires_at <= datetime.now(UTC):
        return None
    device.last_used_at = datetime.now(UTC)
    device.last_used_ip = ip
    return device


async def list_trusted_devices(
    db: AsyncSession, user_id: uuid.UUID,
) -> list[TrustedDevice]:
    """List all non-expired trusted devices for a user, newest first."""
    now = datetime.now(UTC)
    result = await db.execute(
        select(TrustedDevice)
        .where(TrustedDevice.user_id == user_id)
        .where(TrustedDevice.expires_at > now)
        .order_by(TrustedDevice.created_at.desc()),
    )
    return list(result.scalars().all())


async def revoke_trusted_device(
    db: AsyncSession, user_id: uuid.UUID, device_id: uuid.UUID,
) -> bool:
    """Delete one of the user's trusted devices. Returns True if a row
    was deleted, False if no match (wrong owner or already gone)."""
    result = await db.execute(
        delete(TrustedDevice)
        .where(TrustedDevice.id == device_id)
        .where(TrustedDevice.user_id == user_id),
    )
    return (result.rowcount or 0) > 0


async def revoke_all_trusted_devices(
    db: AsyncSession, user_id: uuid.UUID,
) -> int:
    """Delete every trusted device for a user. Called on password change,
    TOTP disable, TOTP re-enrolment, and account deletion."""
    result = await db.execute(
        delete(TrustedDevice).where(TrustedDevice.user_id == user_id),
    )
    return result.rowcount or 0
