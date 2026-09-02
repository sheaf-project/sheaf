import hashlib
import logging
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
from sheaf.observability.usage import record_active_account
from sheaf.request import client_ip
from sheaf.request_context import set_request_origin

logger = logging.getLogger("sheaf")

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
            logger.warning(
                "auth: unknown API key presented (possible credential scan): "
                "ip=%s",
                client_ip(request),
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

        # Check expiry
        if api_key.expires_at is not None:
            from datetime import UTC, datetime

            if datetime.now(UTC) > api_key.expires_at:
                logger.info(
                    "auth: expired API key used: api_key_id=%s user=%s ip=%s",
                    api_key.id,
                    api_key.user_id,
                    client_ip(request),
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="API key expired"
                )

        # Store scopes on request state — used by require_scope()
        request.state.api_key_scopes = set(api_key.scopes)
        request.state.api_key_id = api_key.id
        request.state.auth_method = "api_key"

        # last_used_at is a fire-and-forget metric write, done in its own
        # short-lived, immediately-committed session rather than by dirtying
        # the request-session `api_key`. On a long-lived request (an SSE
        # stream) the request transaction stays open for the whole connection,
        # so an UPDATE flushed into it would hold a write lock on the api_keys
        # row for the entire stream and block every other request using the
        # same key, timing them out (statement_timeout) to a 500.
        from datetime import UTC, datetime

        from sqlalchemy import update

        from sheaf.database import async_session_factory

        try:
            async with async_session_factory() as _touch_db:
                await _touch_db.execute(
                    update(ApiKey)
                    .where(ApiKey.id == api_key.id)
                    .values(last_used_at=datetime.now(UTC))
                )
                await _touch_db.commit()
        except Exception:
            pass  # best-effort: never fail authentication on a metric write

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
                logger.warning(
                    "auth: access token used after its session was revoked "
                    "(possible token replay): session=%s user=%s ip=%s",
                    jwt_sid,
                    payload.get("sub"),
                    client_ip(request),
                )
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
            # Track the validated session id for admin step-up and the
            # session-management endpoints. Only stamp it here, inside the
            # branch that actually validated the cookie: an API-key or
            # bearer request may carry a stray sheaf_session cookie we never
            # checked, and treating that unvalidated value as a live session
            # would let a scoped key mint a full child session off a made-up
            # cookie (the /sessions/secondary parent contract). A JWT bound
            # to a session already set request.state.session_id above.
            request.state.session_id = session_id
            # Update last-active metadata
            await touch_session(
                session_id, ip=client_ip(request),
            )

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        logger.warning(
            "auth: valid credential resolved to a missing user (dangling "
            "credential / data inconsistency): user=%s method=%s ip=%s",
            user_id,
            getattr(request.state, "auth_method", None),
            client_ip(request),
        )
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
    # pending_deletion: allowed through here on purpose. The account is inside
    # its deletion grace window and the user must be able to sign in, look at
    # what they are about to lose, export it, and change their mind - so the
    # state cannot be a blanket 403 the way SUSPENDED and BANNED are. Mutations
    # are the part that has to be refused, and that is `block_pending_deletion`
    # below, applied per-router.

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

    # Aggregate usage metrics (DAU/MAU). Best-effort and non-blocking: this only
    # schedules a fire-and-forget task, so Redis latency or an outage can never
    # delay or fail auth. This is the single choke point that sees every auth
    # method (API key, JWT, session cookie) with the account resolved. The kind
    # splits interactive client use (session cookie or JWT bearer) from
    # automation (API key) into separate sketches. Only the id-free aggregate
    # cardinality is ever published; the id is folded one-way into a Redis HLL
    # sketch and never stored.
    _usage_kind = (
        "api"
        if getattr(request.state, "auth_method", None) == "api_key"
        else "client"
    )
    record_active_account(user.id, _usage_kind)

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


def block_pending_deletion(user: User) -> None:
    """Refuse a mutation from an account that has asked to be deleted.

    The mutation half of the PENDING_DELETION state. `get_current_user` lets
    these accounts authenticate, because the deletion grace window is there for
    the user to reconsider and they cannot reconsider from behind a 403 - they
    need to read their data, export it, and cancel. What they must not do is
    keep BUILDING, and on the sharing surface that is not a tidiness argument:
    a grant minted during the window is an exposure whose owner has already
    announced they will not be around to manage it, pointing at data the
    deletion sweep is going to remove underneath it.

    409, not 403: nothing is wrong with the caller's credentials or their
    permissions. The account is in a state that conflicts with the request, and
    the fix is an action the user can take themselves, which the detail names.

    Deliberately a plain function rather than a dependency: the callers here
    already have the resolved `User` in hand, and a second dependency would
    re-run the whole auth chain. Cancelling the deletion (auth.py) must never
    call this - that is the way out.
    """
    if user.account_status == AccountStatus.PENDING_DELETION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This account is scheduled for deletion. Cancel the deletion "
                "in account settings before making changes."
            ),
        )


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

        logger.info(
            "auth: API key missing required scope: scope=%s api_key_id=%s "
            "user=%s ip=%s",
            scope,
            getattr(request.state, "api_key_id", None),
            user.id,
            client_ip(request),
        )
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
            logger.warning(
                "auth: non-admin API key reached admin endpoint "
                "(privilege probe): api_key_id=%s user=%s ip=%s",
                getattr(request.state, "api_key_id", None),
                user.id,
                client_ip(request),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing scope: admin:read",
            )
    elif not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    # Stamp the request origin so log_admin_action can record where this
    # admin acted from without threading `request` through every caller.
    set_request_origin(client_ip(request), request.headers.get("user-agent"))
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
            logger.warning(
                "auth: API key without admin:write reached admin-write "
                "endpoint (privilege probe): api_key_id=%s user=%s ip=%s",
                getattr(request.state, "api_key_id", None),
                user.id,
                client_ip(request),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing scope: admin:write",
            )
    elif not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    # Stamp the request origin so log_admin_action can record where this
    # admin acted from without threading `request` through every caller.
    set_request_origin(client_ip(request), request.headers.get("user-agent"))
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
