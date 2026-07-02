import hashlib
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, EmailStr
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, get_current_user_allow_unverified
from sheaf.auth.jwt import TokenType, create_token, decode_token
from sheaf.auth.lockout import ensure_not_locked, record_login_failure
from sheaf.auth.passwords import (
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from sheaf.auth.sessions import (
    cache_refresh_rotation,
    consume_refresh_jti,
    create_session,
    delete_all_user_sessions,
    delete_other_sessions,
    delete_session,
    get_cached_refresh_rotation,
    list_user_sessions,
    register_refresh_jti,
    rename_session,
    resolve_session_handle,
    revoke_refresh_jti,
    session_handle,
)
from sheaf.auth.totp import (
    TotpCheck,
    check_code_once,
    generate_recovery_codes,
    generate_secret,
    get_provisioning_uri,
    totp_error_detail,
)
from sheaf.auth.trusted_devices import (
    TRUSTED_DEVICE_COOKIE,
    TRUSTED_DEVICE_TTL_DAYS,
    list_trusted_devices,
    mint_trusted_device,
    revoke_all_trusted_devices,
    revoke_trusted_device,
    verify_trusted_device,
)
from sheaf.config import SheafMode, read_custom_support_text, settings
from sheaf.crypto import blind_index, decrypt, encrypt, hash_mail_token
from sheaf.database import get_db
from sheaf.image_processing import animation_allowed
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.activity_event import ActivityAction
from sheaf.models.api_key import ApiKey
from sheaf.models.security_event import SecurityEventType
from sheaf.models.system import DeleteConfirmation, System
from sheaf.models.trusted_device import TrustedDevice
from sheaf.models.user import AccountStatus, User, UserTier
from sheaf.observability.metrics import (
    auth_logins_total,
    auth_password_reset_total,
    auth_recovery_codes_used_total,
)
from sheaf.redact import redact_email
from sheaf.request import client_ip
from sheaf.schemas.user import (
    SecondarySessionRequest,
    SecondarySessionResponse,
    TokenRefresh,
    TokenResponse,
    TOTPSetupRequest,
    TOTPSetupResponse,
    TOTPVerify,
    UserLogin,
    UserRead,
    UserRegister,
    UserUpdate,
)
from sheaf.services import captcha
from sheaf.services.activity_log import log_activity
from sheaf.services.security_events import record_security_event

_VALID_SCOPES = {
    "system:read", "system:write",
    "members:read", "members:write", "members:delete",
    "fronts:read", "fronts:write", "fronts:delete",
    "groups:read", "groups:write", "groups:delete",
    "tags:read", "tags:write", "tags:delete",
    "fields:read", "fields:write", "fields:delete",
    "journals:read", "journals:write", "journals:delete",
    "settings:read", "settings:write", "settings:delete",
    "notifications:read", "notifications:write", "notifications:delete",
    "polls:read", "polls:write", "polls:delete",
    "messages:read", "messages:write", "messages:delete",
    "import:write",
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


def _cookie_secure() -> bool:
    """Whether to mark auth cookies as Secure.

    Browsers silently drop Secure cookies on plain-HTTP origins, which would
    break refresh-token rotation in HTTP dev setups. Default to Secure
    (production-safe); only relax when sheaf_base_url is explicitly http://.
    """
    return not settings.sheaf_base_url.startswith("http://")


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
        "account_deletion_grace_days": settings.account_deletion_grace_days,
        "file_cdn_base": settings.s3_public_url.rstrip("/") or None,
        "terms_url": settings.terms_url or None,
        "privacy_url": settings.privacy_url or None,
        "support_email": settings.support_email or None,
        "support_url": settings.support_url or None,
        "support_note": settings.support_note or None,
        "support_custom_text": read_custom_support_text(),
        "status_url": settings.status_url or None,
        "captcha_provider": settings.captcha_provider or None,
        "captcha_on_login": captcha.required_for_login(),
    }


@router.get(
    "/captcha/challenge",
    dependencies=[rate_limit(20, 60, fail_closed=True)],
)
async def get_captcha_challenge():
    """Issue a captcha challenge for the login/register widget."""
    if not captcha.required_for_signup():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Captcha is not enabled",
        )
    return captcha.issue_challenge()


def _hash_recovery_code(code: str) -> str:
    """Hash a recovery code for storage."""
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def _store_recovery_codes(user: User, codes: list[str]) -> None:
    """Hash and store recovery codes as encrypted JSON."""
    hashed = [_hash_recovery_code(c) for c in codes]
    user.recovery_codes = encrypt(json.dumps(hashed))


async def _check_recovery_code(db: AsyncSession, user: User, code: str) -> bool:
    """Check a recovery code and consume it if valid.

    Uses a conditional UPDATE (WHERE recovery_codes = <old ciphertext>) so two
    concurrent logins presenting the same code can't both succeed. Fernet
    re-randomises the IV on every encrypt, so the ciphertext comparison is a
    reliable "I saw this exact version" check.
    """
    old_blob = user.recovery_codes
    if not old_blob:
        return False
    try:
        hashed_codes = json.loads(decrypt(old_blob))
    except Exception:
        return False
    code_hash = _hash_recovery_code(code)
    if code_hash not in hashed_codes:
        return False

    remaining = [c for c in hashed_codes if c != code_hash]
    new_blob = encrypt(json.dumps(remaining))

    result = await db.execute(
        update(User)
        .where(User.id == user.id, User.recovery_codes == old_blob)
        .values(recovery_codes=new_blob)
    )
    if result.rowcount != 1:
        return False
    # Drop the ORM's stale copy so a later session flush doesn't overwrite
    # our conditional update (or a concurrent winner's write) with the
    # in-memory ciphertext.
    db.expire(user, ["recovery_codes"])
    return True


async def _mint_refresh_token(user_id, session_id: str) -> str:
    """Create a rotating refresh JWT and register its jti in Redis.

    Refresh tokens carry a one-shot jti. /refresh consumes the jti on use
    (GETDEL) and issues a new one; a second attempt with the same jti trips
    reuse detection and kills the session. Binding jti→session_id in Redis
    also lets logout revoke the refresh path even if the attacker has a
    cached copy of the cookie.
    """
    jti = secrets.token_urlsafe(24)
    ttl = settings.jwt_refresh_token_expire_days * 86400
    await register_refresh_jti(jti, session_id, ttl)
    return create_token(user_id, TokenType.REFRESH, session_id=session_id, jti=jti)


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
    # Store HMAC-hashed token so a DB leak can't be verified offline.
    user.email_verification_token = hash_mail_token(token)
    user.email_verification_sent_at = datetime.now(UTC)

    subject, html, text = verification_email(token)
    try:
        # force=True: verification is the sanctioned recovery channel for
        # a flagged address, so it must bypass the deliverability gate -
        # otherwise a user whose mail is blocked could never receive the
        # very link that clears the block (the lockout this whole flow
        # exists to prevent).
        await send_email(email, subject, html, text, kind="verification", force=True)
    except Exception:
        logger.exception("Failed to send verification email to user %s", user.id)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        rate_limit(5, 60, fail_closed=True),
        rate_limit(15, 3600, fail_closed=True),
    ],
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

    if captcha.required_for_signup() and not captcha.verify(body.captcha):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Captcha verification failed",
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

    # The model default is SELF_HOSTED (the right default for a self-hosted
    # instance). In SaaS mode new signups must start on FREE so tier limits
    # (member count, storage quota, etc.) actually apply; admins bump
    # individuals up out of band.
    signup_tier = (
        UserTier.FREE
        if settings.sheaf_mode == SheafMode.SAAS
        else UserTier.SELF_HOSTED
    )

    user = User(
        email=encrypt(body.email),
        email_hash=email_hash,
        password_hash=await hash_password(body.password),
        account_status=account_status,
        email_verified=email_verified,
        signup_ip=client_ip(request),
        tier=signup_tier,
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

    # Track invite code usage. Claim the use atomically: a conditional UPDATE
    # that only increments while the code is under its cap, guarded by
    # rowcount. The earlier _validate_invite_code check is a fast pre-reject,
    # but two concurrent registrations can both pass it on a stale read and a
    # plain use_count += 1 would lose one of the increments, over-redeeming a
    # single-use code. max_uses = 0 means unlimited.
    if invite is not None:
        from sheaf.models.invite_code import InviteCode

        claim = await db.execute(
            update(InviteCode)
            .where(
                InviteCode.id == invite.id,
                or_(
                    InviteCode.max_uses == 0,
                    InviteCode.use_count < InviteCode.max_uses,
                ),
            )
            .values(use_count=InviteCode.use_count + 1)
        )
        if claim.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invite code has reached maximum uses",
            )
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

    await record_security_event(
        event_type=SecurityEventType.REGISTER,
        outcome="success",
        user_id=user.id,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    response.set_cookie(
        key="sheaf_session",
        value=session_id,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
    )

    refresh_token = await _mint_refresh_token(user.id, session_id)
    response.set_cookie(
        key="sheaf_refresh",
        value=refresh_token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=settings.jwt_refresh_token_expire_days * 86400,
        path="/v1/auth",
    )

    return TokenResponse(
        access_token=create_token(user.id, TokenType.ACCESS, session_id=session_id),
        refresh_token=refresh_token,
    )


@router.get("/verify-email", dependencies=[rate_limit(5, 60, fail_closed=True)])
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Verify email address using the token from the verification email."""
    token_hash = hash_mail_token(token)
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
    # Completing verification proves the user controls and can receive at
    # this address, so it clears any deliverability block (hard bounce,
    # complaint, or soft-threshold). This is the escape hatch from the
    # otherwise-permanent lockout a flagged address would cause.
    from sheaf.services.email_events import clear_delivery_state

    clear_delivery_state(user)
    await db.commit()
    return {"verified": True}


@router.post("/resend-verification", dependencies=[rate_limit(3, 60, fail_closed=True)])
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


@router.post(
    "/revalidate-email",
    dependencies=[rate_limit(3, 3600, "user", fail_closed=True)],
)
async def revalidate_email(
    user: User = Depends(get_current_user_allow_unverified),
    db: AsyncSession = Depends(get_db),
):
    """Re-send a verification link to clear a deliverability block.

    For a signed-in user whose current address has been flagged
    undeliverable (hard bounce, complaint, or the soft-bounce
    threshold). The verification send uses force=True so it reaches the
    blocked address, and completing the link clears the block (see
    verify_email + clear_delivery_state). This is the self-service exit
    from what would otherwise be a permanent, admin-only lockout.

    Refuses with 400 when the address isn't flagged, so it can't be used
    as an unmetered mail trigger; throttled the same way as
    resend-verification on top of the per-user rate limit.
    """
    from sheaf.models.user import EmailDeliveryStatus

    if settings.email_backend == "none":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email sending is not configured",
        )

    if (
        user.email_delivery_status == EmailDeliveryStatus.OK
        and not user.email_revalidation_required
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is not flagged for revalidation",
        )

    if user.email_verification_sent_at is not None:
        age = (datetime.now(UTC) - user.email_verification_sent_at).total_seconds()
        if age < 1200:  # 20 minutes between resends, same as resend-verification
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


class PasswordChange(BaseModel):
    current_password: str
    new_password: str
    totp_code: str | None = None


class EmailChange(BaseModel):
    new_email: EmailStr
    current_password: str
    totp_code: str | None = None


async def _deliver_password_reset_email(email: str, token: str, ip: str) -> None:
    """Send the password-reset email. Runs as a background task so the
    request handler returns before the SMTP round-trip — otherwise the
    response time leaks whether the address mapped to a real account."""
    try:
        from sheaf.services.email import send_email
        from sheaf.services.email_templates import password_reset_email

        subject, html, text = password_reset_email(token, ip=ip)
        await send_email(email, subject, html, text, kind="password_reset")
    except Exception:
        logger.exception("Failed to send password reset email")


@router.post("/request-password-reset", dependencies=[rate_limit(3, 60, fail_closed=True)])
async def request_password_reset(
    body: PasswordResetRequest,
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Request a password reset email.

    Always returns 200 to avoid leaking whether the email exists. The
    actual send is deferred to a background task so the response time is
    the same whether or not the address matched an account.
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
                # Still return 200 - don't reveal timing info. Record the
                # throttle: repeated reset requests for a real account from
                # one IP are the abuse signal here.
                await record_security_event(
                    event_type=SecurityEventType.PASSWORD_RESET_REQUEST,
                    outcome="throttled",
                    user_id=user.id,
                    ip=client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                )
                return {"requested": True}

        token = secrets.token_urlsafe(32)
        user.password_reset_token = hash_mail_token(token)
        user.password_reset_sent_at = datetime.now(UTC)
        await db.commit()

        background.add_task(
            _deliver_password_reset_email, body.email, token, client_ip(request)
        )
        await record_security_event(
            event_type=SecurityEventType.PASSWORD_RESET_REQUEST,
            outcome="sent",
            user_id=user.id,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    else:
        # Symmetric work on the no-match branch so timing matches the
        # real path: the token gen + hash equalise CPU, and a commit
        # equalises the DB round-trip the real branch pays for its
        # UPDATE (the initial SELECT already opened a transaction, so
        # this is a real COMMIT to Postgres, not a no-op). The SMTP send
        # is already off the request thread.
        token = secrets.token_urlsafe(32)
        hash_mail_token(token)
        await db.commit()
        await record_security_event(
            event_type=SecurityEventType.PASSWORD_RESET_REQUEST,
            outcome="user_not_found",
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )

    # Counted regardless of whether an email actually went out — the rate
    # at which reset is requested is the signal, not the email count
    # (which is leaky anyway).
    auth_password_reset_total.labels(stage="requested").inc()
    return {"requested": True}


@router.post("/reset-password", dependencies=[rate_limit(5, 60, fail_closed=True)])
async def reset_password(
    body: PasswordReset,
    request: Request,
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

    token_hash = hash_mail_token(body.token)
    result = await db.execute(
        select(User).where(User.password_reset_token == token_hash)
    )
    user = result.scalar_one_or_none()
    if user is None:
        # Unknown / already-consumed token. We can't distinguish "expired
        # and purged" from "never existed" here, so both increment the same
        # stage; the requested-vs-completed funnel still tells the story.
        auth_password_reset_total.labels(stage="expired").inc()
        await record_security_event(
            event_type=SecurityEventType.PASSWORD_RESET_COMPLETE,
            outcome="invalid_token",
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Check 1-hour expiry
    if user.password_reset_sent_at is not None:
        age = (datetime.now(UTC) - user.password_reset_sent_at).total_seconds()
        if age > 3600:
            auth_password_reset_total.labels(stage="expired").inc()
            await record_security_event(
                event_type=SecurityEventType.PASSWORD_RESET_COMPLETE,
                outcome="expired_token",
                user_id=user.id,
                ip=client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset token has expired. Please request a new one.",
            )

    user.password_hash = await hash_password(body.new_password)
    user.password_reset_token = None
    user.password_reset_sent_at = None
    # Reset is the canonical "I've been compromised" recovery flow, so it
    # revokes everything a compromiser could be holding, same as
    # change-password — previously it revoked nothing, leaving an
    # attacker's live session untouched by the victim's recovery. There
    # is no calling session to spare here (the flow is unauthenticated),
    # so ALL sessions die; refresh tokens bound to them fail at /refresh
    # when the session lookup misses. Proving mailbox control also clears
    # the failed-attempt lockout, matching change-password.
    user.failed_login_count = 0
    user.locked_until = None
    await revoke_all_trusted_devices(db, user.id)
    await db.commit()
    await delete_all_user_sessions(user.id)
    auth_password_reset_total.labels(stage="completed").inc()
    await record_security_event(
        event_type=SecurityEventType.PASSWORD_RESET_COMPLETE,
        outcome="success",
        user_id=user.id,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"reset": True}


@router.post(
    "/change-password",
    dependencies=[rate_limit(10, 3600, "user", fail_closed=True)],
)
async def change_password(
    body: PasswordChange,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
):
    """Change the signed-in user's password.

    Gated on the current password and, if TOTP is enabled, a fresh TOTP or
    recovery code. On success all other sessions are revoked so a stolen
    cookie elsewhere can't survive the change; the calling session stays
    alive.
    """

    async def _sec(outcome: str) -> None:
        await record_security_event(
            event_type=SecurityEventType.PASSWORD_CHANGE,
            outcome=outcome,
            user_id=user.id,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
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
    if body.new_password == body.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current password",
        )

    # Step-up credentials are brute-forceable, so this gate consults and
    # feeds the same unified lockout as login.
    ensure_not_locked(user)

    if not await verify_password(body.current_password, user.password_hash):
        await record_login_failure(db, user)
        await _sec("password_incorrect")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    if user.totp_enabled:
        if not body.totp_code:
            await _sec("totp_required")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TOTP code required",
                headers={"X-Sheaf-2FA": "required"},
            )
        secret = decrypt(user.totp_secret)
        totp_result = await check_code_once(user.id, secret, body.totp_code)
        if totp_result is not TotpCheck.OK and not await _check_recovery_code(
            db, user, body.totp_code
        ):
            await record_login_failure(db, user, reason="totp_failures")
            await _sec("totp_invalid")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=totp_error_detail(totp_result),
            )

    user.password_hash = await hash_password(body.new_password)
    user.failed_login_count = 0
    user.locked_until = None
    # Kill any live password-reset token — changing the password is proof
    # the legit user has access, so a phished-but-unredeemed reset link
    # must not outlive this.
    user.password_reset_token = None
    user.password_reset_sent_at = None
    # Revoke every trusted device — a password change is the canonical
    # "kick everything off" event.
    await revoke_all_trusted_devices(db, user.id)
    await log_activity(db, user_id=user.id, action=ActivityAction.PASSWORD_CHANGED)
    await db.commit()

    # Revoke every other session for this user so any lingering copy of a
    # session cookie elsewhere is dead. The calling session stays alive.
    # Refresh tokens bound to revoked sessions fail at /refresh when the
    # session lookup misses.
    revoked = 0
    if session_id:
        revoked = await delete_other_sessions(user.id, session_id)

    await _sec("success")
    return {"changed": True, "revoked_other_sessions": revoked}


@router.post(
    "/change-email",
    dependencies=[rate_limit(10, 3600, "user", fail_closed=True)],
)
async def change_email(
    body: EmailChange,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
):
    """Change the signed-in user's email.

    Gated on the current password and, if TOTP is enabled, a fresh TOTP or
    recovery code. The new address is verified again — verification status
    is reset to false and a verification email is sent. Pre-apply
    verification doesn't actually defend against session compromise (the
    attacker controls the destination inbox); the password+TOTP gate is
    the real protection. The re-verification is a typo safety net.
    Other sessions are revoked, same as change-password.
    """
    new_email = body.new_email.strip().lower()
    current_email = decrypt(user.email).strip().lower()
    if new_email == current_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New email must differ from the current email",
        )

    # Step-up credentials are brute-forceable, so this gate consults and
    # feeds the same unified lockout as login.
    ensure_not_locked(user)

    if not await verify_password(body.current_password, user.password_hash):
        await record_login_failure(db, user)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    if user.totp_enabled:
        if not body.totp_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TOTP code required",
                headers={"X-Sheaf-2FA": "required"},
            )
        secret = decrypt(user.totp_secret)
        totp_result = await check_code_once(user.id, secret, body.totp_code)
        if totp_result is not TotpCheck.OK and not await _check_recovery_code(
            db, user, body.totp_code
        ):
            await record_login_failure(db, user, reason="totp_failures")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=totp_error_detail(totp_result),
            )

    new_hash = blind_index(new_email)
    existing = await db.execute(select(User).where(User.email_hash == new_hash))
    conflict = existing.scalar_one_or_none()
    if conflict is not None and conflict.id != user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already in use",
        )

    user.email = encrypt(new_email)
    user.email_hash = new_hash
    user.email_verified = False
    user.email_verification_token = None
    user.email_verification_sent_at = None
    # A new address starts with a clean deliverability slate - the old
    # address's bounce/complaint history doesn't apply to it. Without
    # this, the row's stale block would (since the gate keys on the
    # row's current hash) silently drop mail to the new address too.
    from sheaf.services.email_events import clear_delivery_state

    clear_delivery_state(user)

    verification_sent = False
    if settings.email_backend != "none":
        await _send_verification_email(db, user, new_email)
        verification_sent = True

    # target_label lands in the unencrypted activity-log column (retained
    # ~a year); the address itself is encrypted at rest two lines up, so
    # store only a redacted form here rather than reintroducing plaintext
    # PII into DB dumps / backups / replicas.
    await log_activity(
        db,
        user_id=user.id,
        action=ActivityAction.EMAIL_CHANGED,
        target_label=redact_email(new_email),
    )
    await db.commit()

    revoked = 0
    if session_id:
        revoked = await delete_other_sessions(user.id, session_id)

    return {
        "email": new_email,
        "verification_sent": verification_sent,
        "revoked_other_sessions": revoked,
    }


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[
        rate_limit(10, 60, fail_closed=True),
        rate_limit(30, 3600, fail_closed=True),
    ],
)
async def login(
    body: UserLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    trusted_device_cookie: str | None = Cookie(
        default=None, alias=TRUSTED_DEVICE_COOKIE,
    ),
):
    # Security-event origin, captured once. The outcome strings mirror
    # the auth_logins_total metric labels so the durable log and the
    # aggregate counter line up. user_id is passed when the account is
    # known (NULL for unknown-email attempts, which we still want for
    # the per-IP stuffing signal without storing the attempted address).
    event_ip = client_ip(request)
    event_ua = request.headers.get("user-agent")

    async def _sec(outcome: str, user_id=None) -> None:
        await record_security_event(
            event_type=SecurityEventType.LOGIN,
            outcome=outcome,
            user_id=user_id,
            ip=event_ip,
            user_agent=event_ua,
        )

    if captcha.required_for_login() and not captcha.verify(body.captcha):
        auth_logins_total.labels(outcome="captcha_failed").inc()
        await _sec("captcha_failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Captcha verification failed",
        )

    email_hash = blind_index(body.email)
    result = await db.execute(select(User).where(User.email_hash == email_hash))
    user = result.scalar_one_or_none()

    # Reject locked accounts before spending argon2 CPU on them. The lockout
    # state leaks that the account exists, but so does a successful login
    # attempt; rate limits + captcha are what stop anonymous enumeration.
    if user is not None:
        try:
            ensure_not_locked(user)
        except HTTPException:
            auth_logins_total.labels(outcome="locked").inc()
            await _sec("locked", user.id)
            raise

    if user is None or not await verify_password(body.password, user.password_hash):
        if user is not None:
            await record_login_failure(db, user)
            auth_logins_total.labels(outcome="password_incorrect").inc()
            await _sec("password_incorrect", user.id)
        else:
            # Spend an equivalent Argon2 verify so an unknown email can't
            # be distinguished from a wrong password by response latency.
            # `or` short-circuits, so verify_password never ran here.
            await dummy_verify()
            auth_logins_total.labels(outcome="user_not_found").inc()
            await _sec("user_not_found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Suspended / banned accounts: refuse the login outright rather
    # than minting a session the user can't use. Past-expiry suspends
    # fall through; the background sweep will normalise the status,
    # and the auth dep also treats them as effectively ACTIVE.
    if user.account_status == AccountStatus.SUSPENDED:
        until = user.suspended_until
        if until is None or until > datetime.now(UTC):
            parts = ["Account suspended"]
            if user.suspended_reason:
                parts.append(f"reason: {user.suspended_reason}")
            if until is not None:
                parts.append(f"until: {until.isoformat()}")
            auth_logins_total.labels(outcome="account_suspended").inc()
            await _sec("account_suspended", user.id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="; ".join(parts),
            )
    if user.account_status == AccountStatus.BANNED:
        auth_logins_total.labels(outcome="account_banned").inc()
        await _sec("account_banned", user.id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account banned",
        )

    # ---- login(): TOTP check + trusted-device handling ----
    # Enforce TOTP if enabled — unless the browser presents a valid
    # trusted-device cookie for this user.
    bypassed_via_trusted_device = False
    recovery_code_used = False
    if user.totp_enabled:
        trusted = await verify_trusted_device(
            db, trusted_device_cookie, user.id, ip=client_ip(request),
        )
        if trusted is not None:
            bypassed_via_trusted_device = True
        else:
            if not body.totp_code:
                auth_logins_total.labels(outcome="totp_required").inc()
                await _sec("totp_required", user.id)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="TOTP code required",
                    headers={"X-Sheaf-2FA": "required"},
                )
            secret = decrypt(user.totp_secret)
            # check_code_once consumes the code (anti-replay): a code
            # seen by a shoulder-surfer or proxy can't be reused at any
            # TOTP gate inside its validity window, and a replayed one is
            # reported distinctly so we tell the user to wait for the next
            # code. Recovery codes are single-use via their own
            # conditional UPDATE.
            totp_result = await check_code_once(user.id, secret, body.totp_code)
            if totp_result is not TotpCheck.OK:
                if await _check_recovery_code(db, user, body.totp_code):
                    recovery_code_used = True
                else:
                    await record_login_failure(db, user, reason="totp_failures")
                    auth_logins_total.labels(outcome="totp_invalid").inc()
                    await _sec("totp_invalid", user.id)
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=totp_error_detail(totp_result),
                    )

    # Rehash if argon2 params have been upgraded
    if needs_rehash(user.password_hash):
        user.password_hash = await hash_password(body.password)

    # Successful login clears any accumulated failure state.
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(UTC)
    # A successful login means the legit user has access; invalidate any
    # outstanding password-reset token so a phished link can't be redeemed
    # after the fact.
    user.password_reset_token = None
    user.password_reset_sent_at = None

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
        secure=_cookie_secure(),
        samesite="lax",
    )

    refresh_token = await _mint_refresh_token(user.id, session_id)
    response.set_cookie(
        key="sheaf_refresh",
        value=refresh_token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=settings.jwt_refresh_token_expire_days * 86400,
        path="/v1/auth",
    )

    # Mint a trusted-device cookie if the user opted in. Only meaningful
    # when TOTP was actually exercised (or already trusted) — without TOTP
    # the cookie wouldn't bypass anything anyway.
    if (
        body.remember_device
        and user.totp_enabled
        and not bypassed_via_trusted_device
    ):
        from sheaf.auth.sessions import _parse_client_name

        ua = request.headers.get("user-agent", "")
        client_header = request.headers.get("x-sheaf-client")
        device_token, _ = await mint_trusted_device(
            db,
            user.id,
            user_agent=ua,
            ip=client_ip(request),
            nickname=body.device_nickname,
            client_name=_parse_client_name(ua, client_header),
        )
        await db.commit()
        response.set_cookie(
            key=TRUSTED_DEVICE_COOKIE,
            value=device_token,
            httponly=True,
            secure=_cookie_secure(),
            samesite="lax",
            max_age=TRUSTED_DEVICE_TTL_DAYS * 86400,
            path="/v1/auth",
        )

    if bypassed_via_trusted_device:
        auth_logins_total.labels(outcome="trusted_device_bypass").inc()
        await _sec("trusted_device_bypass", user.id)
    elif recovery_code_used:
        auth_logins_total.labels(outcome="recovery_code_used").inc()
        auth_recovery_codes_used_total.inc()
        await _sec("recovery_code_used", user.id)
    else:
        auth_logins_total.labels(outcome="success").inc()
        await _sec("success", user.id)

    return TokenResponse(
        access_token=create_token(user.id, TokenType.ACCESS, session_id=session_id),
        refresh_token=refresh_token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
    refresh_cookie: str | None = Cookie(default=None, alias="sheaf_refresh"),
):
    if session_id:
        await delete_session(session_id)
    # Also revoke the refresh jti so a cached copy of the cookie can't be
    # replayed after logout. Best-effort: if the JWT is malformed or lacks
    # a jti (older minted token), skip silently.
    if refresh_cookie:
        try:
            payload = decode_token(refresh_cookie)
            old_jti = payload.get("jti")
            if old_jti:
                await revoke_refresh_jti(old_jti)
        except jwt.PyJWTError:
            pass
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
):
    """List all active sessions for the current user.

    `id` is an opaque handle, not the raw session token - the raw value
    is the `sheaf_session` cookie credential, and listing it here let
    any caller lift a sibling session's cookie and replay it. The
    rename/revoke endpoints below address sessions by this handle.

    is_current uses the request-state session id, which is populated
    for cookie auth and session-bound JWTs alike, so mobile clients get
    a correct marker too.
    """
    current_sid = getattr(request.state, "session_id", None)
    sessions = await list_user_sessions(user.id)
    return [
        {
            "id": session_handle(s["id"]),
            "nickname": s.get("nickname") or None,
            "client_name": s.get("client_name", "Unknown"),
            "created_at": s.get("created_at"),
            "created_ip": s.get("created_ip") or None,
            "last_active_at": s.get("last_active_at"),
            "last_active_ip": s.get("last_active_ip") or None,
            "is_current": s["id"] == current_sid,
        }
        for s in sessions
    ]


@router.patch("/sessions/{target_session_handle}")
async def update_session(
    target_session_handle: str,
    body: SessionRename,
    user: User = Depends(get_current_user),
):
    """Rename a session, addressed by its opaque handle from /sessions.

    Handle resolution walks the caller's own session set, so ownership
    is enforced by construction.
    """
    sid = await resolve_session_handle(user.id, target_session_handle)
    if sid is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    await rename_session(sid, body.nickname)
    return {"ok": True}


@router.delete(
    "/sessions/{target_session_handle}", status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_session(
    request: Request,
    target_session_handle: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a session, addressed by its opaque handle from /sessions.

    Cannot revoke the current session (use /logout).
    """
    sid = await resolve_session_handle(user.id, target_session_handle)
    if sid is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    if sid == getattr(request.state, "session_id", None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot revoke current session. Use /logout instead.",
        )
    await delete_session(sid)
    await log_activity(db, user_id=user.id, action=ActivityAction.SESSION_REVOKED)
    await db.commit()


@router.post("/sessions/revoke-others")
async def revoke_other_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
):
    """Revoke all sessions except the current one."""
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No current session",
        )
    revoked = await delete_other_sessions(user.id, session_id)
    await log_activity(
        db,
        user_id=user.id,
        action=ActivityAction.SESSION_REVOKED,
        detail={"revoked": revoked},
    )
    await db.commit()
    return {"revoked": revoked}


@router.post(
    "/sessions/secondary",
    response_model=SecondarySessionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit(10, 3600, "user", fail_closed=True)],
)
async def create_secondary_session(
    request: Request,
    body: SecondarySessionRequest | None = None,
    user: User = Depends(get_current_user),
):
    """Mint a child session + refresh token for a paired companion device.

    Use case: an iOS app pairing a watchOS app. Both devices used to share
    one refresh token, which serialised them through the one-shot rotation
    and made the watch's offline-then-refresh path collide with the phone's.
    The phone now calls this endpoint after login and ships the returned
    tokens to the watch via WatchConnectivity, so each device rotates
    independently.

    The new session is registered as a child of the caller's session: when
    the parent is revoked (logout, /sessions DELETE, change-password) the
    child is cascaded automatically, matching the user expectation that
    "kicking out my phone also kicks out its watch."
    """
    parent_sid = getattr(request.state, "session_id", None)
    if not parent_sid:
        # API-key callers don't have a session, and minting a child without
        # a parent would defeat the cascade contract.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A session-bound caller is required to mint a secondary session",
        )

    client_name = (body.client_name if body and body.client_name else None) or (
        request.headers.get("x-sheaf-client")
    )

    child_sid = await create_session(
        user.id,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
        client_header=client_name,
        parent_session_id=parent_sid,
    )

    refresh_token = await _mint_refresh_token(user.id, child_sid)
    access_token = create_token(user.id, TokenType.ACCESS, session_id=child_sid)

    return SecondarySessionResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        # Opaque handle, matching what /sessions lists. The raw child
        # sid is a cookie-grade credential; the companion device only
        # needs the tokens, and the handle is what display/management
        # calls (rename, revoke) address.
        session_id=session_handle(child_sid),
    )


# ---------------------------------------------------------------------------
# Trusted devices
# ---------------------------------------------------------------------------


class TrustedDeviceRename(BaseModel):
    nickname: str


@router.get("/trusted-devices")
async def get_trusted_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trusted_cookie: str | None = Cookie(default=None, alias=TRUSTED_DEVICE_COOKIE),
):
    """List the user's non-expired trusted devices.

    `is_current` is true for the device whose cookie is on this request.
    """
    devices = await list_trusted_devices(db, user.id)
    current_hash = None
    if trusted_cookie:
        from sheaf.auth.trusted_devices import _hash_token

        current_hash = _hash_token(trusted_cookie)
    # Legacy rows have client_name="" (server-default backfill from the
    # migration). For those, re-parse user_agent on the fly so the UI
    # still shows something better than "Unknown". New rows always
    # populate client_name at mint time and are returned as-is.
    from sheaf.auth.sessions import _parse_client_name

    return [
        {
            "id": str(d.id),
            "nickname": d.nickname,
            "user_agent": d.user_agent,
            "client_name": d.client_name or _parse_client_name(d.user_agent),
            "created_at": d.created_at,
            "created_ip": d.created_ip,
            "last_used_at": d.last_used_at,
            "last_used_ip": d.last_used_ip,
            "expires_at": d.expires_at,
            "is_current": d.token_hash == current_hash,
        }
        for d in devices
    ]


@router.patch("/trusted-devices/{device_id}")
async def rename_trusted_device(
    device_id: uuid.UUID,
    body: TrustedDeviceRename,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a trusted device."""
    result = await db.execute(
        select(TrustedDevice)
        .where(TrustedDevice.id == device_id)
        .where(TrustedDevice.user_id == user.id),
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Device not found",
        )
    device.nickname = body.nickname[:128] if body.nickname else None
    await db.commit()
    return {"ok": True}


@router.delete(
    "/trusted-devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_trusted_device_endpoint(
    device_id: uuid.UUID,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trusted_cookie: str | None = Cookie(default=None, alias=TRUSTED_DEVICE_COOKIE),
):
    """Revoke a trusted device. If the caller revoked the device tied to
    this browser, also clear the cookie so the next login requires TOTP
    again."""
    # Look up the row first so we can compare its hash against the cookie
    # before deletion.
    result = await db.execute(
        select(TrustedDevice)
        .where(TrustedDevice.id == device_id)
        .where(TrustedDevice.user_id == user.id),
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Device not found",
        )
    revoked_self = False
    if trusted_cookie:
        from sheaf.auth.trusted_devices import _hash_token

        revoked_self = _hash_token(trusted_cookie) == device.token_hash
    device_label = device.nickname or None
    await revoke_trusted_device(db, user.id, device_id)
    await log_activity(
        db,
        user_id=user.id,
        action=ActivityAction.TRUSTED_DEVICE_REVOKED,
        target_label=device_label,
    )
    await db.commit()
    if revoked_self:
        response.delete_cookie(TRUSTED_DEVICE_COOKIE, path="/v1/auth")


@router.post("/trusted-devices/revoke-all")
async def revoke_all_trusted_devices_endpoint(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke every trusted device for the user. Clears this browser's
    cookie too."""
    revoked = await revoke_all_trusted_devices(db, user.id)
    await log_activity(
        db,
        user_id=user.id,
        action=ActivityAction.TRUSTED_DEVICE_REVOKED,
        detail={"revoked": revoked},
    )
    await db.commit()
    response.delete_cookie(TRUSTED_DEVICE_COOKIE, path="/v1/auth")
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
        jti = payload.get("jti")
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from exc

    # Consume the old jti atomically. GETDEL ensures only one of N parallel
    # callers wins; the rest see None. None means either (a) genuine reuse —
    # likely theft, kill the session — or (b) a concurrent legitimate caller
    # raced and lost (StrictMode double-fire, parallel queries on page load,
    # multiple tabs). To distinguish, the winner caches its rotation result
    # for a few seconds; losers within that window replay it instead of
    # tripping the kill-session path. Outside the grace window, treat as
    # reuse and burn the session.
    if jti is None or sid is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    consumed_sid = await consume_refresh_jti(jti)
    replay_token: str | None = None
    if consumed_sid is None:
        replay_token = await get_cached_refresh_rotation(jti)
        if replay_token is None:
            await delete_session(sid)
            response.delete_cookie("sheaf_session")
            response.delete_cookie("sheaf_refresh", path="/v1/auth")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )

    # Verify the session still exists (may have been revoked out-of-band).
    from sheaf.auth.sessions import get_session_user_id

    if await get_session_user_id(sid) is None:
        response.delete_cookie("sheaf_session")
        response.delete_cookie("sheaf_refresh", path="/v1/auth")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked",
        )

    if replay_token is not None:
        new_refresh = replay_token
    else:
        new_refresh = await _mint_refresh_token(user_id, sid)
        await cache_refresh_rotation(jti, new_refresh)
    response.set_cookie(
        key="sheaf_refresh",
        value=new_refresh,
        httponly=True,
        secure=_cookie_secure(),
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
    deletion_scheduled = None
    if user.deletion_requested_at:
        deletion_scheduled = user.deletion_requested_at + timedelta(
            days=settings.account_deletion_grace_days
        )
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
        deletion_scheduled_for=deletion_scheduled,
        newsletter_opt_in=user.newsletter_opt_in,
        email_delivery_status=user.email_delivery_status.value,
        email_revalidation_required=user.email_revalidation_required,
        disable_cdn_during_ddos=user.disable_cdn_during_ddos,
        uploads_allowed=(
            user.is_admin or settings.allow_image_uploads or user.can_upload_images
        ),
        bio_uploads_allowed=(
            (user.is_admin or settings.allow_image_uploads or user.can_upload_images)
            and (user.is_admin or settings.allow_bio_images or user.can_upload_images)
        ),
        external_images_allowed=settings.allow_external_images,
        animated_uploads_allowed=animation_allowed(user, settings),
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

    if (
        body.disable_cdn_during_ddos is not None
        and body.disable_cdn_during_ddos != user.disable_cdn_during_ddos
    ):
        # Persist regardless of settings.shield_mode_enabled — the user
        # may set the preference on a selfhost instance now and migrate
        # to a SaaS deployment later, or vice versa. The flag is only
        # acted on when the cf-shield script flips state, so it's a
        # no-op on instances where the feature isn't wired.
        user.disable_cdn_during_ddos = body.disable_cdn_during_ddos

    await db.commit()
    await db.refresh(user)

    email_verified = user.email_verified or settings.email_verification != "required"
    deletion_scheduled = None
    if user.deletion_requested_at:
        deletion_scheduled = user.deletion_requested_at + timedelta(
            days=settings.account_deletion_grace_days
        )
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
        deletion_scheduled_for=deletion_scheduled,
        newsletter_opt_in=user.newsletter_opt_in,
        email_delivery_status=user.email_delivery_status.value,
        email_revalidation_required=user.email_revalidation_required,
        disable_cdn_during_ddos=user.disable_cdn_during_ddos,
        uploads_allowed=(
            user.is_admin or settings.allow_image_uploads or user.can_upload_images
        ),
        bio_uploads_allowed=(
            (user.is_admin or settings.allow_image_uploads or user.can_upload_images)
            and (user.is_admin or settings.allow_bio_images or user.can_upload_images)
        ),
        external_images_allowed=settings.allow_external_images,
        animated_uploads_allowed=animation_allowed(user, settings),
    )


@router.post(
    "/totp/setup",
    response_model=TOTPSetupResponse,
    dependencies=[rate_limit(5, 60, "user", fail_closed=True)],
)
async def totp_setup(
    body: TOTPSetupRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Begin TOTP enrolment. Requires the account password.

    Without the password gate, a session-only attacker could enrol an
    attacker-controlled secret + recovery codes and turn a stolen session
    into durable account capture (change-password / change-email / disable
    would then demand a code only the attacker has). Enabling a factor is
    held to the same re-auth standard as disabling one.
    """
    if user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is already enabled",
        )

    ensure_not_locked(user)

    if not await verify_password(body.password, user.password_hash):
        await record_login_failure(db, user)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
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
    totp_result = await check_code_once(user.id, secret, body.code)
    if totp_result is not TotpCheck.OK:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=totp_error_detail(totp_result),
        )

    user.totp_enabled = True
    await log_activity(db, user_id=user.id, action=ActivityAction.TOTP_ENABLED)
    await db.commit()


@router.post(
    "/totp/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[rate_limit(5, 60, "user", fail_closed=True)],
)
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

    ensure_not_locked(user)

    if not await verify_password(body.password, user.password_hash):
        await record_login_failure(db, user)
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
    totp_result = await check_code_once(user.id, secret, body.totp_code)
    if totp_result is not TotpCheck.OK and not await _check_recovery_code(
        db, user, body.totp_code
    ):
        await record_login_failure(db, user, reason="totp_failures")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=totp_error_detail(totp_result),
        )

    # If System Safety requires TOTP for destructive actions, disabling it
    # here would silently weaken that gate (verify_destructive_auth would
    # fall back to password-only). Block until the tier is lowered.
    sys_row = await db.execute(select(System).where(System.user_id == user.id))
    system = sys_row.scalar_one_or_none()
    if system is not None and system.delete_confirmation in (
        DeleteConfirmation.TOTP,
        DeleteConfirmation.BOTH,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "System Safety requires TOTP for destructive actions. "
                "Lower that confirmation setting before disabling 2FA."
            ),
        )

    user.totp_enabled = False
    user.totp_secret = None
    user.recovery_codes = None
    user.failed_login_count = 0
    user.locked_until = None
    # Trusted devices were minted under the old TOTP relationship; wipe
    # them so a stale cookie can't bypass anything if TOTP is re-enabled.
    await revoke_all_trusted_devices(db, user.id)
    await log_activity(db, user_id=user.id, action=ActivityAction.TOTP_DISABLED)
    await db.commit()


@router.post(
    "/totp/regenerate-recovery-codes",
    dependencies=[rate_limit(5, 60, "user", fail_closed=True)],
)
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

    ensure_not_locked(user)

    secret = decrypt(user.totp_secret)
    totp_result = await check_code_once(user.id, secret, body.code)
    if totp_result is not TotpCheck.OK:
        await record_login_failure(db, user, reason="totp_failures")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=totp_error_detail(totp_result),
        )

    codes = generate_recovery_codes()
    _store_recovery_codes(user, codes)
    user.failed_login_count = 0
    user.locked_until = None
    await log_activity(
        db, user_id=user.id, action=ActivityAction.RECOVERY_CODES_REGENERATED
    )
    await db.commit()
    return {"recovery_codes": codes}


def _reject_api_key_auth(request: Request) -> None:
    """Block API-key auth from the key-management endpoints.

    An API key minting or revoking keys is privilege escalation: a leaked
    read-only key could create a fresh write/delete key (and outlive its own
    revocation). Key management is a session/JWT-only operation, same posture
    as the account + async-export endpoints.
    """
    if getattr(request.state, "auth_method", None) == "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys cannot manage API keys. Sign in with a session or JWT.",
        )


@router.get("/keys", response_model=list[ApiKeyRead])
async def list_api_keys(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's API keys (never returns plaintext key)."""
    _reject_api_key_auth(request)
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
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new API key. The plaintext key is returned once — save it."""
    _reject_api_key_auth(request)
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
    await log_activity(
        db,
        user_id=user.id,
        action=ActivityAction.API_KEY_CREATED,
        target_label=api_key.name,
    )
    await db.commit()
    await db.refresh(api_key)

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
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API key. Only the owning user can revoke their own keys."""
    _reject_api_key_auth(request)
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    key_name = api_key.name
    await db.delete(api_key)
    await log_activity(
        db,
        user_id=user.id,
        action=ActivityAction.API_KEY_REVOKED,
        target_label=key_name,
    )
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

    # Verify password. Step-up credentials feed the unified lockout,
    # same as login.
    ensure_not_locked(user)

    if not await verify_password(body.password, user.password_hash):
        await record_login_failure(db, user)
        # 403: step-up auth denial. See system_safety.verify_destructive_auth
        # for full reasoning.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
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
        totp_result = await check_code_once(user.id, totp_secret, body.totp_code)
        if totp_result is not TotpCheck.OK and not await _check_recovery_code(
            db, user, body.totp_code
        ):
            await record_login_failure(db, user, reason="totp_failures")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=totp_error_detail(totp_result),
            )

    now = datetime.now(UTC)
    user.account_status = AccountStatus.PENDING_DELETION
    user.deletion_requested_at = now
    user.deletion_reminders_sent = None

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
            await send_email(email, subject, html, text, kind="deletion_confirmed")
        except Exception:
            logger.exception("Failed to send deletion confirmation email")

    await log_activity(
        db, user_id=user.id, action=ActivityAction.ACCOUNT_DELETION_SCHEDULED
    )
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
    await log_activity(
        db, user_id=user.id, action=ActivityAction.ACCOUNT_DELETION_CANCELLED
    )
    await db.commit()

    return {"cancelled": True}
