import hashlib
import uuid
from collections.abc import Callable

import jwt
from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.jwt import TokenType, decode_token
from sheaf.auth.sessions import check_admin_step_up, get_session_user_id, touch_session
from sheaf.database import get_db
from sheaf.models.user import AccountStatus, User
from sheaf.request import client_ip

_bearer_scheme = HTTPBearer(auto_error=False)

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
        request.state.auth_method = "api_key"

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
            request.state.auth_method = "jwt"
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            ) from exc

        # If the JWT is bound to a session, verify it still exists
        jwt_sid = payload.get("sid")
        if jwt_sid is not None:
            if await get_session_user_id(jwt_sid) is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session revoked",
                )
            request.state.session_id = jwt_sid
            await touch_session(
                jwt_sid, ip=client_ip(request),
            )

    # Fall back to session cookie
    if user_id is None and session_id is not None:
        user_id = await get_session_user_id(session_id)
        if user_id is not None:
            request.state.auth_method = "session"
            # Update last-active metadata
            await touch_session(
                session_id, ip=client_ip(request),
            )

    # Track session_id from cookie for admin step-up auth
    if session_id is not None and not hasattr(request.state, "session_id"):
        request.state.session_id = session_id

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

    # Check account status. SUSPENDED carries an optional expiry +
    # reason; we render those into the detail so the user knows what
    # happened and when it lifts. Past-expiry suspends are treated as
    # effectively ACTIVE: the background sweep will clear the state
    # at its next tick, and we don't want to wedge a returning user
    # in the gap between expiry and the next run.
    if user.account_status == AccountStatus.SUSPENDED:
        from datetime import UTC, datetime

        until = user.suspended_until
        if until is not None and until <= datetime.now(UTC):
            pass  # fall through; treat as active
        else:
            parts = ["Account suspended"]
            if user.suspended_reason:
                parts.append(f"reason: {user.suspended_reason}")
            if until is not None:
                parts.append(f"until: {until.isoformat()}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="; ".join(parts),
            )
    if user.account_status == AccountStatus.BANNED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account banned",
        )
    if (
        user.account_status == AccountStatus.PENDING_APPROVAL
        and not getattr(request.state, "_allow_pending_approval", False)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account pending approval",
        )
    # pending_deletion: allow login but block via separate check on mutations

    # Check email verification (skip if explicitly allowed, e.g. resend-verification)
    if not user.email_verified and not getattr(request.state, "_skip_email_verification", False):
        from sheaf.config import settings

        if settings.email_verification == "required":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email not verified",
            )

    # For session/JWT auth, no scope restrictions
    if not hasattr(request.state, "api_key_scopes"):
        request.state.api_key_scopes = None

    # Expose user ID on request state for rate limiting and logging
    request.state.user_id = str(user.id)

    return user


async def get_current_user_allow_unverified(
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
) -> User:
    """Like get_current_user but skips the email verification check.

    Used for endpoints that unverified users need access to (e.g. resend-verification).
    Still enforces account status checks (suspended/banned).
    """
    request.state._skip_email_verification = True
    request.state._allow_pending_approval = True
    return await get_current_user(request, db, credentials, session_id)


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

        # write and delete both imply read; nothing implies delete
        if scope.endswith(":read"):
            resource = scope.split(":")[0]
            if f"{resource}:write" in scopes or f"{resource}:delete" in scopes:
                return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing scope: {scope}",
        )

    return dep


async def _check_admin_step_up(request: Request, user: User) -> None:
    """Raise 403 if admin step-up auth is required but not completed.

    Step-up is tracked per (user, session) in Redis — completing it in
    one session does not unlock any other live session on the account,
    so a stolen token can't piggyback on the real admin's step-up.
    Session cookies and session-bound JWTs both carry a session id; only
    API key auth is exempt — scoped API keys are already explicit
    programmatic credentials.
    """
    from sheaf.config import settings

    if settings.admin_auth_level == "none":
        return
    if getattr(request.state, "auth_method", None) == "api_key":
        return
    session_id = getattr(request.state, "session_id", None)
    if not await check_admin_step_up(user.id, session_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin_step_up_required",
        )


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
    await _check_admin_step_up(request, user)
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
    await _check_admin_step_up(request, user)
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
