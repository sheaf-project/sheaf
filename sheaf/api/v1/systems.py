from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, get_current_user_optional, require_scope
from sheaf.auth.passwords import verify_password
from sheaf.auth.totp import verify_code
from sheaf.crypto import decrypt, encrypt
from sheaf.database import get_db
from sheaf.models.system import DeleteConfirmation, PrivacyLevel, System
from sheaf.models.user import User
from sheaf.schemas.system import DeleteConfirmationUpdate, SystemRead, SystemUpdate

router = APIRouter(prefix="/systems", tags=["systems"])


def _system_to_read(system: System) -> SystemRead:
    """Build a SystemRead with `note` decrypted to plaintext.

    System.note is the only encrypted-at-rest field on System (description
    is plaintext for historical reasons), so we just patch it through here
    rather than building a parallel `decrypt_system_for_read` helper."""
    plaintext_note = decrypt(system.note) if system.note else None
    return SystemRead.model_validate(
        {**system.__dict__, "note": plaintext_note}
    )


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")
    return system


@router.get("/me", response_model=SystemRead)
async def get_own_system(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    return _system_to_read(system)


@router.patch(
    "/me",
    response_model=SystemRead,
    dependencies=[Depends(require_scope("system:write"))],
)
async def update_own_system(
    body: SystemUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key == "note":
            # Encrypt at rest. Empty string clears the column (notes are
            # overwrite-only, no revisions).
            if value is None or value == "":
                system.note = None
            else:
                system.note = encrypt(value)
        else:
            setattr(system, key, value)
    await db.commit()
    await db.refresh(system)
    return _system_to_read(system)


@router.put(
    "/me/delete-confirmation",
    response_model=SystemRead,
    dependencies=[Depends(require_scope("system:write"))],
)
async def update_delete_confirmation(
    body: DeleteConfirmationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update delete confirmation level. Requires password (+ TOTP if enabled)."""
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    if user.totp_enabled:
        if not body.totp_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TOTP code required",
            )
        secret = decrypt(user.totp_secret)
        if not verify_code(secret, body.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code",
            )

    # Don't allow setting to TOTP/both if TOTP isn't enabled
    if body.level in (DeleteConfirmation.TOTP, DeleteConfirmation.BOTH) and not user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot require TOTP confirmation without 2FA enabled",
        )

    system = await _get_user_system(user, db)
    system.delete_confirmation = body.level
    await db.commit()
    await db.refresh(system)
    return _system_to_read(system)


@router.get("/{system_id}", response_model=SystemRead)
async def get_system(
    system_id: str,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    result = await db.execute(select(System).where(System.id == system_id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")

    # Privacy check — only return if public (friends/auth checks come later)
    if system.privacy != PrivacyLevel.PUBLIC and (user is None or system.user_id != user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")

    return _system_to_read(system)
