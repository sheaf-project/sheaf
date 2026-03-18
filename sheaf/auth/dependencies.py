import uuid

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.jwt import TokenType, decode_token
from sheaf.auth.sessions import get_session_user_id
from sheaf.database import get_db
from sheaf.models.user import User

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
) -> User:
    """Authenticate via JWT bearer token OR session cookie.

    JWT is checked first. If no JWT is present, falls back to session cookie.
    """
    user_id: uuid.UUID | None = None

    # Try JWT first
    if credentials is not None:
        try:
            payload = decode_token(credentials.credentials)
            if payload.get("type") != TokenType.ACCESS.value:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type",
                )
            user_id = uuid.UUID(payload["sub"])
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            ) from exc

    # Fall back to session cookie
    if user_id is None and session_id is not None:
        user_id = await get_session_user_id(session_id)

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user


async def get_current_user_optional(
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
) -> User | None:
    """Like get_current_user but returns None instead of 401 if not authenticated."""
    user_id: uuid.UUID | None = None

    if credentials is not None:
        try:
            payload = decode_token(credentials.credentials)
            if payload.get("type") == TokenType.ACCESS.value:
                user_id = uuid.UUID(payload["sub"])
        except (jwt.PyJWTError, ValueError, KeyError):
            return None

    if user_id is None and session_id is not None:
        user_id = await get_session_user_id(session_id)

    if user_id is None:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
