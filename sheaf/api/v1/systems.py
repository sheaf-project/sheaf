from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.auth.lockout import ensure_not_locked, record_login_failure
from sheaf.auth.passwords import verify_password
from sheaf.auth.totp import TotpCheck, check_code_once, totp_error_detail
from sheaf.crypto import decrypt, encrypt
from sheaf.database import get_db
from sheaf.files import owned_avatar_url, owned_description_urls
from sheaf.models.system import DeleteConfirmation, System
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
    # Drop avatar/bio media referencing another account's storage keys before
    # it is stored (and later re-signed on read) - cross-tenant read oracle.
    if "avatar_url" in update_data:
        update_data["avatar_url"] = owned_avatar_url(update_data["avatar_url"], user.id)
    if "description" in update_data:
        update_data["description"] = owned_description_urls(
            update_data["description"], user.id
        )
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
    # Brute-forceable credentials are verified here, so the attempt feeds
    # the unified lockout the same way login does.
    ensure_not_locked(user)

    if not await verify_password(body.password, user.password_hash):
        await record_login_failure(db, user)
        # 403: step-up auth denial. See system_safety.verify_destructive_auth
        # for full reasoning.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid password",
        )

    if user.totp_enabled:
        if not body.totp_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TOTP code required",
            )
        secret = decrypt(user.totp_secret)
        totp_result = await check_code_once(user.id, secret, body.totp_code)
        if totp_result is not TotpCheck.OK:
            await record_login_failure(db, user, reason="totp_failures")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=totp_error_detail(totp_result),
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
    user: User = Depends(get_current_user),
):
    """Fetch a system by id. Owner-only.

    This endpoint used to honour `privacy=public` by returning the full
    owner view of any public system to any authenticated caller — which
    leaked the decrypted private `note` and the `delete_confirmation`
    tier cross-tenant. Nothing consumes a public read path today (the
    web client only uses /systems/me and there is no discovery surface),
    so cross-tenant reads are closed entirely until public profiles ship
    as a designed feature with a dedicated, fail-closed public schema.
    The `privacy` field itself is kept and remains user-settable; it just
    grants nothing yet.
    """
    result = await db.execute(select(System).where(System.id == system_id))
    system = result.scalar_one_or_none()
    # Same 404 for "doesn't exist" and "not yours" — no existence oracle.
    if system is None or system.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")

    return _system_to_read(system)
