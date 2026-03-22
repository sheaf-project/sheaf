import hashlib
import uuid
from collections.abc import Callable

import jwt
from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.jwt import TokenType, decode_token
from sheaf.auth.sessions import get_session_user_id
from sheaf.database import get_db
from sheaf.models.user import User

_bearer_scheme = HTTPBearer(auto_error=False)

_ALL_SCOPES = {
    "system:read", "system:write",
    "members:read", "members:write", "members:delete",
    "fronts:read", "fronts:write", "fronts:delete",
    "groups:read", "groups:write", "groups:delete",
    "tags:read", "tags:write", "tags:delete",
    "fields:read", "fields:write", "fields:delete",
    "export:read",
    "admin:read", "admin:write",
}


def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
) -> User:
    """Authenticate via API key, JWT bearer token, or session cookie.

    Priority: API key (sk_ prefix) → JWT → session cookie.
    For API key auth, scopes are stored on request.state.api_key_scopes.
    For session/JWT auth, request.state.api_key_scopes is None (full access).
    """
    user_id: uuid.UUID | None = None

    # Try API key first (prefix sk_)
    if credentials is not None and credentials.credentials.startswith("sk_"):
        from sheaf.models.api_key import ApiKey

        key_hash = _hash_key(credentials.credentials)
        result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key = result.scalar_one_or_none()

        if api_key is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

        # Check expiry
        if api_key.expires_at is not None:
            from datetime import UTC, datetime

            if datetime.now(UTC) > api_key.expires_at:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="API key expired"
                )

        # Store scopes on request state — used by require_scope()
        request.state.api_key_scopes = set(api_key.scopes)
        request.state.api_key_id = api_key.id

        # Fire-and-forget last_used_at update
        from datetime import UTC, datetime

        api_key.last_used_at = datetime.now(UTC)

        user_id = api_key.user_id

    # Try JWT bearer token
    elif credentials is not None:
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

    # For session/JWT auth, no scope restrictions
    if not hasattr(request.state, "api_key_scopes"):
        request.state.api_key_scopes = None

    return user


def require_scope(scope: str) -> Callable:
    """Dependency factory — enforces a scope when auth is via API key.

    Session/JWT auth bypasses scope checks (full access, existing behaviour).
    Write scopes imply read: having `members:write` satisfies `members:read`.
    Delete scopes are explicit — `members:write` does NOT imply `members:delete`.
    """

    async def dep(request: Request, user: User = Depends(get_current_user)) -> User:
        scopes = request.state.api_key_scopes
        if scopes is None:
            return user  # session/JWT: unrestricted

        if scope in scopes:
            return user

        # write implies read, but nothing implies delete
        if scope.endswith(":read"):
            resource = scope.split(":")[0]
            if f"{resource}:write" in scopes:
                return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing scope: {scope}",
        )

    return dep


async def get_admin_user(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Require admin access — either is_admin=True (session/JWT) or admin:* scope (API key)."""
    scopes = request.state.api_key_scopes
    if scopes is not None:
        if not any(s.startswith("admin:") for s in scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing scope: admin:read",
            )
    elif not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def get_admin_write_user(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Require admin write access — is_admin=True (session/JWT) or admin:write scope (API key)."""
    scopes = request.state.api_key_scopes
    if scopes is not None:
        if "admin:write" not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing scope: admin:write",
            )
    elif not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
) -> User | None:
    """Like get_current_user but returns None instead of 401 if not authenticated."""
    user_id: uuid.UUID | None = None

    if credentials is not None and not credentials.credentials.startswith("sk_"):
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
    if not hasattr(request.state, "api_key_scopes"):
        request.state.api_key_scopes = None
    return result.scalar_one_or_none()
