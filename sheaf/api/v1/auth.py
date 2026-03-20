import hashlib
import json
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.auth.jwt import TokenType, create_token, decode_token
from sheaf.auth.passwords import hash_password, needs_rehash, verify_password
from sheaf.auth.sessions import create_session, delete_session
from sheaf.auth.totp import (
    generate_recovery_codes,
    generate_secret,
    get_provisioning_uri,
    verify_code,
)
from sheaf.config import settings
from sheaf.crypto import blind_index, decrypt, encrypt
from sheaf.database import get_db
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.user import (
    TokenRefresh,
    TokenResponse,
    TOTPSetupResponse,
    TOTPVerify,
    UserLogin,
    UserRead,
    UserRegister,
)

router = APIRouter(prefix="/auth", tags=["auth"])


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


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: UserRegister,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    email_hash = blind_index(body.email)

    existing = await db.execute(select(User).where(User.email_hash == email_hash))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=encrypt(body.email),
        email_hash=email_hash,
        password_hash=hash_password(body.password),
    )
    db.add(user)

    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc

    # Auto-create a system for the user
    system = System(user_id=user.id, name="My System")
    db.add(system)

    # Create session before committing so a Redis failure rolls back the DB
    session_id = await create_session(user.id)

    await db.commit()

    response.set_cookie(
        key="sheaf_session",
        value=session_id,
        httponly=True,
        secure=True,
        samesite="lax",
    )

    refresh_token = create_token(user.id, TokenType.REFRESH)
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
        access_token=create_token(user.id, TokenType.ACCESS),
        refresh_token=refresh_token,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: UserLogin,
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
    session_id = await create_session(user.id)

    await db.commit()

    response.set_cookie(
        key="sheaf_session",
        value=session_id,
        httponly=True,
        secure=True,
        samesite="lax",
    )

    refresh_token = create_token(user.id, TokenType.REFRESH)
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
        access_token=create_token(user.id, TokenType.ACCESS),
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
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from exc

    new_refresh = create_token(user_id, TokenType.REFRESH)
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
        access_token=create_token(user_id, TokenType.ACCESS),
        refresh_token=new_refresh,
    )


@router.get("/me", response_model=UserRead)
async def get_me(user: User = Depends(get_current_user)):
    return UserRead(
        id=user.id,
        email=decrypt(user.email),
        totp_enabled=user.totp_enabled,
        tier=user.tier.value,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.post("/totp/setup", response_model=TOTPSetupResponse)
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

    return TOTPSetupResponse(
        secret=secret,
        provisioning_uri=uri,
        recovery_codes=recovery_codes,
    )


@router.post("/totp/verify", status_code=status.HTTP_204_NO_CONTENT)
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
