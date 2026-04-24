import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt

from sheaf.config import settings

JWT_AUDIENCE = "sheaf-api"


def _jwt_issuer() -> str:
    """The issuer claim. Prefer sheaf_base_url if configured so tokens are
    tied to the exact origin; fall back to "sheaf" for installs with no
    explicit base URL."""
    return settings.sheaf_base_url or "sheaf"


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def create_token(
    user_id: uuid.UUID,
    token_type: TokenType,
    session_id: str | None = None,
    jti: str | None = None,
) -> str:
    if token_type == TokenType.ACCESS:
        expires = timedelta(minutes=settings.jwt_access_token_expire_minutes)
    else:
        expires = timedelta(days=settings.jwt_refresh_token_expire_days)

    payload = {
        "sub": str(user_id),
        "type": token_type.value,
        "exp": datetime.now(UTC) + expires,
        "iat": datetime.now(UTC),
        "iss": _jwt_issuer(),
        "aud": JWT_AUDIENCE,
    }
    if session_id is not None:
        payload["sid"] = session_id
    if jti is not None:
        payload["jti"] = jti
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises jwt.PyJWTError on failure.

    Enforces the iss and aud claims; tokens issued before these were added
    fail here and the caller treats them as invalid (user re-logs in).
    """
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        audience=JWT_AUDIENCE,
        issuer=_jwt_issuer(),
        options={"require": ["exp", "iat", "sub", "iss", "aud"]},
    )
