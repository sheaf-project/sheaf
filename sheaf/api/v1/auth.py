import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, get_current_user_allow_unverified
from sheaf.auth.jwt import TokenType, create_token, decode_token
from sheaf.auth.passwords import hash_password, needs_rehash, verify_password
from sheaf.auth.sessions import (
    create_session,
    delete_other_sessions,
    delete_session,
    list_user_sessions,
    rename_session,
)
from sheaf.auth.totp import (
    generate_recovery_codes,
    generate_secret,
    get_provisioning_uri,
    verify_code,
)
from sheaf.config import settings
from sheaf.crypto import blind_index, decrypt, encrypt
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.api_key import ApiKey
from sheaf.models.system import System
from sheaf.models.user import AccountStatus, User
from sheaf.request import client_ip
from sheaf.schemas.user import (
    TokenRefresh,
    TokenResponse,
    TOTPSetupResponse,
    TOTPVerify,
    UserLogin,
    UserRead,
    UserRegister,
    UserUpdate,
)

_VALID_SCOPES = {
    "system:read", "system:write",
    "members:read", "members:write", "members:delete",
    "fronts:read", "fronts:write", "fronts:delete",
    "groups:read", "groups:write", "groups:delete",
    "tags:read", "tags:write", "tags:delete",
    "fields:read", "fields:write", "fields:delete",
    "export:read",
    "admin:read", "admin:write",
}
_ADMIN_SCOPES = {"admin:read", "admin:write"}


class ApiKeyCreate(BaseModel):
    name: str
    scopes: list[str]
    expires_at: datetime | None = None


class ApiKeyRead(BaseModel):
    id: str
    name: str
    scopes: list[str]
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyRead):
    key: str  # plaintext, returned once only

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("sheaf.auth")


@router.get("/config")
async def get_auth_config():
    """Public endpoint returning registration settings for the login UI."""
    invite_enabled = (
        settings.registration_mode == "invite" or settings.invite_codes_enabled
    )
    return {
        "registration_mode": settings.registration_mode,
        "invite_codes_enabled": invite_enabled,
        "email_verification": settings.email_verification,
        "email_enabled": settings.email_backend != "none",
        "base_url": settings.sheaf_base_url or None,
    }


def _hash_recovery_code(code: str) -> str:
    """Hash a recovery code for storage."""
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def _store_recovery_codes(user: User, codes: list[str]) -> None:
    """Hash and store recovery codes as encrypted JSON."""
    hashed = [_hash_recovery_code(c) for c in codes]
    user.recovery_codes = encrypt(json.dumps(hashed))


def _check_recovery_code(user: User, code: str) -> bool:
    """Check a recovery code and consume it if valid."""
    if not user.recovery_codes:
        return False
    try:
        hashed_codes = json.loads(decrypt(user.recovery_codes))
    except Exception:
        return False
    code_hash = _hash_recovery_code(code)
    if code_hash in hashed_codes:
        hashed_codes.remove(code_hash)
        user.recovery_codes = encrypt(json.dumps(hashed_codes))
        return True
    return False


async def _validate_invite_code(db: AsyncSession, code: str):
    """Validate and return an invite code, or raise 400/403."""
    from sheaf.models.invite_code import InviteCode

    result = await db.execute(select(InviteCode).where(InviteCode.code == code))
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid invite code",
        )
    if invite.expires_at is not None and datetime.now(UTC) > invite.expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite code expired",
        )
    if invite.max_uses > 0 and invite.use_count >= invite.max_uses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite code has reached maximum uses",
        )
    return invite


async def _send_verification_email(db: AsyncSession, user: "User", email: str) -> None:
    """Generate a verification token and send the verification email."""
    from sheaf.services.email import send_email
    from sheaf.services.email_templates import verification_email

    token = secrets.token_urlsafe(32)
    # Store hashed token on the user (reuse crypto for consistency)
    user.email_verification_token = hashlib.sha256(token.encode()).hexdigest()
    user.email_verification_sent_at = datetime.now(UTC)

    subject, html, text = verification_email(token)
    try:
        await send_email(email, subject, html, text)
    except Exception:
        logger.exception("Failed to send verification email to user %s", user.id)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit(5, 60), rate_limit(15, 3600)],
)
async def register(
    body: UserRegister,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    # Check registration mode
    reg_mode = settings.registration_mode
    if reg_mode == "closed":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed",
        )

    # Validate invite code if required or optionally provided
    invite = None
    if reg_mode == "invite":
        if not body.invite_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invite code required",
            )
        invite = await _validate_invite_code(db, body.invite_code)
    elif body.invite_code and settings.invite_codes_enabled:
        invite = await _validate_invite_code(db, body.invite_code)

    email_hash = blind_index(body.email)

    existing = await db.execute(select(User).where(User.email_hash == email_hash))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    # Determine initial account status
    from sheaf.models.user import AccountStatus

    if reg_mode == "approval" and invite is None:
        account_status = AccountStatus.PENDING_APPROVAL
    else:
        account_status = AccountStatus.ACTIVE
    email_verified = settings.email_verification != "required"

    user = User(
        email=encrypt(body.email),
        email_hash=email_hash,
        password_hash=hash_password(body.password),
        account_status=account_status,
        email_verified=email_verified,
        signup_ip=client_ip(request),
        newsletter_opt_in=body.newsletter_opt_in,
        newsletter_opted_in_at=datetime.now(UTC) if body.newsletter_opt_in else None,
    )
    db.add(user)

    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc

    # Track invite code usage
    if invite is not None:
        invite.use_count += 1
        user.invite_code_id = invite.id

    # Auto-create a system for the user
    system = System(user_id=user.id, name="My System")
    db.add(system)

    # Send verification email if required
    if not email_verified and settings.email_backend != "none":
        await _send_verification_email(db, user, body.email)

    # Create session before committing so a Redis failure rolls back the DB
    session_id = await create_session(
        user.id,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
        client_header=request.headers.get("x-sheaf-client"),
    )

    await db.commit()

    response.set_cookie(
        key="sheaf_session",
        value=session_id,
        httponly=True,
        secure=True,
        samesite="lax",
    )

    refresh_token = create_token(user.id, TokenType.REFRESH, session_id=session_id)
    response.set_cookie(
        key="sheaf_refresh",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.jwt_refresh_token_expire_days * 86400,
        path="/v1/auth",
    )

    return TokenResponse(
        access_token=create_token(user.id, TokenType.ACCESS, session_id=session_id),
        refresh_token=refresh_token,
    )


@router.get("/verify-email", dependencies=[rate_limit(5, 60)])
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Verify email address using the token from the verification email."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(
        select(User).where(User.email_verification_token == token_hash)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    # Check expiry (24 hours)
    if user.email_verification_sent_at is not None:
        age = (datetime.now(UTC) - user.email_verification_sent_at).total_seconds()
        if age > 86400:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification link expired. Request a new one.",
            )

    user.email_verified = True
    user.email_verification_token = None
    user.email_verification_sent_at = None
    await db.commit()
    return {"verified": True}


@router.post("/resend-verification", dependencies=[rate_limit(3, 60)])
async def resend_verification(
    user: User = Depends(get_current_user_allow_unverified),
    db: AsyncSession = Depends(get_db),
):
    """Resend the email verification link."""
    if user.email_verified or settings.email_verification != "required":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already verified",
        )

    # Rate limit: 3 per hour
    if user.email_verification_sent_at is not None:
        age = (datetime.now(UTC) - user.email_verification_sent_at).total_seconds()
        if age < 1200:  # 20 minutes between resends
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Please wait before requesting another verification email",
            )

    email = decrypt(user.email)
    await _send_verification_email(db, user, email)
    await db.commit()
    return {"sent": True}


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

class PasswordResetRequest(BaseModel):
    email: str


class PasswordReset(BaseModel):
    token: str
    new_password: str


@router.post("/request-password-reset", dependencies=[rate_limit(3, 60)])
async def request_password_reset(
    body: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Request a password reset email.

    Always returns 200 to avoid leaking whether the email exists.
    """
    if settings.email_backend == "none":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is not configured on this server",
        )

    email_hash = blind_index(body.email)
    result = await db.execute(select(User).where(User.email_hash == email_hash))
    user = result.scalar_one_or_none()

    if user is not None:
        # Rate limit
        if user.password_reset_sent_at is not None:
            age = (datetime.now(UTC) - user.password_reset_sent_at).total_seconds()
            if age < settings.password_reset_rate_limit_minutes * 60:
                # Still return 200 — don't reveal timing info
                return {"requested": True}

        token = secrets.token_urlsafe(32)
        user.password_reset_token = hashlib.sha256(token.encode()).hexdigest()
        user.password_reset_sent_at = datetime.now(UTC)
        await db.commit()

        try:
            from sheaf.services.email import send_email
            from sheaf.services.email_templates import password_reset_email

            requester_ip = client_ip(request)
            subject, html, text = password_reset_email(token, ip=requester_ip)
            await send_email(body.email, subject, html, text)
        except Exception:
            logger.exception("Failed to send password reset email")

    return {"requested": True}


@router.post("/reset-password", dependencies=[rate_limit(5, 60)])
async def reset_password(
    body: PasswordReset,
    db: AsyncSession = Depends(get_db),
):
    """Reset password using a token from the password reset email."""
    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )
    if len(body.new_password) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at most 128 characters",
        )

    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    result = await db.execute(
        select(User).where(User.password_reset_token == token_hash)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Check 1-hour expiry
    if user.password_reset_sent_at is not None:
        age = (datetime.now(UTC) - user.password_reset_sent_at).total_seconds()
        if age > 3600:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset token has expired. Please request a new one.",
            )

    user.password_hash = hash_password(body.new_password)
    user.password_reset_token = None
    user.password_reset_sent_at = None
    await db.commit()
    return {"reset": True}


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[rate_limit(10, 60), rate_limit(30, 3600)],
)
async def login(
    body: UserLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    email_hash = blind_index(body.email)
    result = await db.execute(select(User).where(User.email_hash == email_hash))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Enforce TOTP if enabled
    if user.totp_enabled:
        if not body.totp_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TOTP code required",
                headers={"X-Sheaf-2FA": "required"},
            )
        secret = decrypt(user.totp_secret)
        if not verify_code(secret, body.totp_code) and not _check_recovery_code(
            user, body.totp_code
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code",
            )

    # Rehash if argon2 params have been upgraded
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    user.last_login_at = datetime.now(UTC)

    # Create session before committing so a Redis failure rolls back the DB
    session_id = await create_session(
        user.id,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
        client_header=request.headers.get("x-sheaf-client"),
    )

    await db.commit()

    response.set_cookie(
        key="sheaf_session",
        value=session_id,
        httponly=True,
        secure=True,
        samesite="lax",
    )

    refresh_token = create_token(user.id, TokenType.REFRESH, session_id=session_id)
    response.set_cookie(
        key="sheaf_refresh",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.jwt_refresh_token_expire_days * 86400,
        path="/v1/auth",
    )

    return TokenResponse(
        access_token=create_token(user.id, TokenType.ACCESS, session_id=session_id),
        refresh_token=refresh_token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
):
    if session_id:
        await delete_session(session_id)
    response.delete_cookie("sheaf_session")
    response.delete_cookie("sheaf_refresh", path="/v1/auth")


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class SessionRename(BaseModel):
    nickname: str


@router.get("/sessions")
async def get_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
):
    """List all active sessions for the current user."""
    sessions = await list_user_sessions(user.id)
    return [
        {
            "id": s["id"],
            "nickname": s.get("nickname") or None,
            "client_name": s.get("client_name", "Unknown"),
            "created_at": s.get("created_at"),
            "created_ip": s.get("created_ip") or None,
            "last_active_at": s.get("last_active_at"),
            "last_active_ip": s.get("last_active_ip") or None,
            "is_current": s["id"] == session_id,
        }
        for s in sessions
    ]


@router.patch("/sessions/{target_session_id}")
async def update_session(
    target_session_id: str,
    body: SessionRename,
    user: User = Depends(get_current_user),
):
    """Rename a session. Only the owning user can rename their sessions."""
    from sheaf.auth.sessions import get_session_info

    info = await get_session_info(target_session_id)
    if info is None or info.get("user_id") != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    await rename_session(target_session_id, body.nickname)
    return {"ok": True}


@router.delete(
    "/sessions/{target_session_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_session(
    target_session_id: str,
    user: User = Depends(get_current_user),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
):
    """Revoke a specific session. Cannot revoke the current session (use /logout)."""
    if target_session_id == session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot revoke current session. Use /logout instead.",
        )
    from sheaf.auth.sessions import get_session_info

    info = await get_session_info(target_session_id)
    if info is None or info.get("user_id") != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    await delete_session(target_session_id)


@router.post("/sessions/revoke-others")
async def revoke_other_sessions(
    user: User = Depends(get_current_user),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
):
    """Revoke all sessions except the current one."""
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No current session",
        )
    revoked = await delete_other_sessions(user.id, session_id)
    return {"revoked": revoked}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    body: TokenRefresh | None = None,
    refresh_cookie: str | None = Cookie(default=None, alias="sheaf_refresh"),
):
    # Accept refresh token from body (API clients) or HttpOnly cookie (web)
    token = (body.refresh_token if body and body.refresh_token else None) or refresh_cookie
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token provided",
        )

    try:
        payload = decode_token(token)
        if payload.get("type") != TokenType.REFRESH.value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )
        from uuid import UUID

        user_id = UUID(payload["sub"])
        sid = payload.get("sid")
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from exc

    # If the token is bound to a session, verify the session still exists.
    if sid is not None:
        from sheaf.auth.sessions import get_session_user_id

        if await get_session_user_id(sid) is None:
            response.delete_cookie("sheaf_session")
            response.delete_cookie("sheaf_refresh", path="/v1/auth")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session revoked",
            )

    new_refresh = create_token(user_id, TokenType.REFRESH, session_id=sid)
    response.set_cookie(
        key="sheaf_refresh",
        value=new_refresh,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.jwt_refresh_token_expire_days * 86400,
        path="/v1/auth",
    )

    return TokenResponse(
        access_token=create_token(user_id, TokenType.ACCESS, session_id=sid),
        refresh_token=new_refresh,
    )


@router.get("/me", response_model=UserRead)
async def get_me(user: User = Depends(get_current_user_allow_unverified)):
    # Only flag email as unverified if the server actually requires verification
    email_verified = user.email_verified or settings.email_verification != "required"
    return UserRead(
        id=user.id,
        email=decrypt(user.email),
        totp_enabled=user.totp_enabled,
        is_admin=user.is_admin,
        tier=user.tier.value,
        account_status=user.account_status,
        email_verified=email_verified,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        deletion_requested_at=user.deletion_requested_at,
        newsletter_opt_in=user.newsletter_opt_in,
        email_delivery_status=user.email_delivery_status.value,
        email_revalidation_required=user.email_revalidation_required,
    )


@router.patch("/me", response_model=UserRead)
async def update_me(
    body: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.newsletter_opt_in is not None and body.newsletter_opt_in != user.newsletter_opt_in:
        user.newsletter_opt_in = body.newsletter_opt_in
        user.newsletter_opted_in_at = datetime.now(UTC) if body.newsletter_opt_in else None

    await db.commit()
    await db.refresh(user)

    email_verified = user.email_verified or settings.email_verification != "required"
    return UserRead(
        id=user.id,
        email=decrypt(user.email),
        totp_enabled=user.totp_enabled,
        is_admin=user.is_admin,
        tier=user.tier.value,
        account_status=user.account_status,
        email_verified=email_verified,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        deletion_requested_at=user.deletion_requested_at,
        newsletter_opt_in=user.newsletter_opt_in,
        email_delivery_status=user.email_delivery_status.value,
        email_revalidation_required=user.email_revalidation_required,
    )


@router.post(
    "/totp/setup",
    response_model=TOTPSetupResponse,
    dependencies=[rate_limit(5, 60, "user")],
)
async def totp_setup(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is already enabled",
        )

    secret = generate_secret()
    email = decrypt(user.email)
    uri = get_provisioning_uri(secret, email)
    recovery_codes = generate_recovery_codes()

    # Store encrypted secret and recovery codes (not yet enabled — needs verification)
    user.totp_secret = encrypt(secret)
    _store_recovery_codes(user, recovery_codes)
    await db.commit()

    return TOTPSetupResponse(
        secret=secret,
        provisioning_uri=uri,
        recovery_codes=recovery_codes,
    )


@router.post(
    "/totp/verify",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[rate_limit(5, 60, "user")],
)
async def totp_verify(
    body: TOTPVerify,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.totp_secret is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run /totp/setup first",
        )

    secret = decrypt(user.totp_secret)
    if not verify_code(secret, body.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code",
        )

    user.totp_enabled = True
    await db.commit()


@router.post("/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(
    body: UserLogin,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable TOTP. Requires password + current TOTP code for confirmation."""
    if not user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not enabled",
        )

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    if not body.totp_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP code required to disable 2FA",
        )

    secret = decrypt(user.totp_secret)
    if not verify_code(secret, body.totp_code) and not _check_recovery_code(
        user, body.totp_code
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code",
        )

    user.totp_enabled = False
    user.totp_secret = None
    user.recovery_codes = None
    await db.commit()


@router.post("/totp/regenerate-recovery-codes")
async def regenerate_recovery_codes(
    body: TOTPVerify,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate recovery codes. Requires a valid TOTP code to authorize."""
    if not user.totp_enabled or not user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not enabled",
        )

    secret = decrypt(user.totp_secret)
    if not verify_code(secret, body.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code",
        )

    codes = generate_recovery_codes()
    _store_recovery_codes(user, codes)
    await db.commit()
    return {"recovery_codes": codes}


@router.get("/keys", response_model=list[ApiKeyRead])
async def list_api_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's API keys (never returns plaintext key)."""
    result = await db.execute(select(ApiKey).where(ApiKey.user_id == user.id))
    return [
        ApiKeyRead(
            id=str(k.id),
            name=k.name,
            scopes=k.scopes,
            last_used_at=k.last_used_at,
            expires_at=k.expires_at,
            created_at=k.created_at,
        )
        for k in result.scalars()
    ]


@router.post(
    "/keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit(10, 60, "user")],
)
async def create_api_key(
    body: ApiKeyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new API key. The plaintext key is returned once — save it."""
    unknown = set(body.scopes) - _VALID_SCOPES
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown scopes: {sorted(unknown)}",
        )

    # Non-admin users cannot request admin scopes
    requested_admin = set(body.scopes) & _ADMIN_SCOPES
    if requested_admin and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin scopes require an admin account",
        )

    plaintext = "sk_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()

    api_key = ApiKey(
        user_id=user.id,
        name=body.name,
        key_hash=key_hash,
        scopes=body.scopes,
        expires_at=body.expires_at,
    )
    db.add(api_key)
    await db.flush()

    return ApiKeyCreated(
        id=str(api_key.id),
        name=api_key.name,
        scopes=api_key.scopes,
        last_used_at=None,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
        key=plaintext,
    )


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API key. Only the owning user can revoke their own keys."""
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    await db.delete(api_key)
    await db.commit()


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------


class DeleteAccountRequest(BaseModel):
    password: str
    totp_code: str | None = None


@router.post("/delete-account", dependencies=[rate_limit(3, 60, "user")])
async def request_account_deletion(
    body: DeleteAccountRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Request account deletion with a grace period."""
    if user.account_status == AccountStatus.PENDING_DELETION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is already scheduled for deletion",
        )

    # Verify password
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
        )

    # Verify TOTP if enabled
    if user.totp_enabled:
        if not body.totp_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TOTP code required",
                headers={"X-Sheaf-2FA": "required"},
            )
        totp_secret = decrypt(user.totp_secret)
        if not verify_code(totp_secret, body.totp_code):
            # Check recovery codes
            valid_recovery = False
            if user.recovery_codes:
                stored_codes = json.loads(decrypt(user.recovery_codes))
                candidate = hashlib.sha256(
                    body.totp_code.strip().lower().encode()
                ).hexdigest()
                if candidate in stored_codes:
                    stored_codes.remove(candidate)
                    user.recovery_codes = encrypt(json.dumps(stored_codes))
                    valid_recovery = True
            if not valid_recovery:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid TOTP code",
                )

    now = datetime.now(UTC)
    user.account_status = AccountStatus.PENDING_DELETION
    user.deletion_requested_at = now
    user.deletion_reminders_sent = None

    from datetime import timedelta

    deletion_date = now + timedelta(days=settings.account_deletion_grace_days)

    # Send confirmation email
    if settings.email_backend != "none":
        try:
            from sheaf.services.email import send_email
            from sheaf.services.email_templates import deletion_confirmation_email

            email = decrypt(user.email)
            subject, html, text = deletion_confirmation_email(
                deletion_date.strftime("%B %d, %Y")
            )
            await send_email(email, subject, html, text)
        except Exception:
            logger.exception("Failed to send deletion confirmation email")

    await db.commit()

    return {
        "deletion_scheduled_for": deletion_date.isoformat(),
        "grace_days": settings.account_deletion_grace_days,
    }


@router.post("/cancel-deletion")
async def cancel_account_deletion(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a pending account deletion."""
    if user.account_status != AccountStatus.PENDING_DELETION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending deletion to cancel",
        )

    user.account_status = AccountStatus.ACTIVE
    user.deletion_requested_at = None
    user.deletion_reminders_sent = None
    await db.commit()

    return {"cancelled": True}
