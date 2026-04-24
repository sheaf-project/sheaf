import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt

from sheaf.config import settings


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
    }
    if session_id is not None:
        payload["sid"] = session_id
    if jti is not None:
        payload["jti"] = jti
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
