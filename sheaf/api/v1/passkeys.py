"""Passkey enrolment and management: `/v1/auth/passkeys`.

Sign-in is not here yet; this is the half where an account gains and
manages the credentials, and nothing on this router can get anyone into an
account. The rules it enforces, from the passkey design:

* Every endpoint 404s when the availability rule says the instance cannot
  offer passkeys (no https base URL, or a refused RP override). "Not
  available on this instance" must never look like "broken".
* Enrolment requires the password, plus a TOTP code whenever the account
  has TOTP. Failures feed the same unified lockout as login.
* Removal is never password-gated. Deleting a key narrows the account rather
  than weakening it, and the person who cannot recall the password must be
  able to remove a key that was just stolen. The last key is deletable too:
  the password always remains, so there is no lockout to interlock against.
* API keys are refused throughout. A leaked key of any scope must not be
  able to mint a credential that signs in, nor list which ones exist.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# The recovery-code check is the one login and change-password use; the
# step-up here has to accept exactly what they accept, so it is borrowed
# rather than copied.
from sheaf.api.v1.auth import _check_recovery_code
from sheaf.auth.dependencies import get_current_user
from sheaf.auth.lockout import ensure_not_locked, record_login_failure
from sheaf.auth.passkey_ceremony import (
    PasskeyCeremonyError,
    challenge_from_credential,
    registration_options,
    store_registration_challenge,
    take_registration_challenge,
    verify_enrolment,
)
from sheaf.auth.passkeys import RelyingParty, current_relying_party
from sheaf.auth.passwords import verify_password
from sheaf.auth.totp import TotpCheck, check_code_once, totp_error_detail
from sheaf.crypto import decrypt
from sheaf.database import get_db
from sheaf.encrypted_fields import user_email_aad, user_totp_secret_aad
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.activity_event import ActivityAction
from sheaf.models.passkey_credential import PasskeyCredential
from sheaf.models.security_event import SecurityEventType
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.request import client_ip
from sheaf.schemas.passkey import (
    PasskeyRead,
    PasskeyRegisterBegin,
    PasskeyRegisterBeginResponse,
    PasskeyRegisterComplete,
    PasskeyUpdate,
)
from sheaf.services.activity_log import log_activity
from sheaf.services.security_events import record_security_event

logger = logging.getLogger("sheaf")

router = APIRouter(prefix="/auth/passkeys", tags=["passkeys"])

_UNAVAILABLE_DETAIL = "Passkeys are not available on this instance."


def _relying_party() -> RelyingParty:
    """The instance's relying party, or a 404 that says the feature is off.

    The same reason string the config endpoint will expose later; here it
    is a 404 rather than a 400 because from a client's point of view the
    route does not exist on this instance.
    """
    resolution = current_relying_party()
    if resolution.rp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_UNAVAILABLE_DETAIL,
        )
    return resolution.rp


def _refuse_api_keys(request: Request) -> None:
    if getattr(request.state, "auth_method", None) == "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "API keys cannot manage passkeys. Sign in with a session or "
                "JWT to enrol or remove one."
            ),
        )


def _to_read(row: PasskeyCredential, rp: RelyingParty) -> PasskeyRead:
    return PasskeyRead(
        id=row.id,
        nickname=row.nickname,
        rp_id=row.rp_id,
        usable=(row.rp_id == rp.rp_id),
        transports=list(row.transports or []),
        aaguid=row.aaguid,
        backup_eligible=row.backup_eligible,
        backup_state=row.backup_state,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


async def _own_credentials(db: AsyncSession, user: User) -> list[PasskeyCredential]:
    result = await db.execute(
        select(PasskeyCredential)
        .where(PasskeyCredential.user_id == user.id)
        .order_by(PasskeyCredential.created_at)
    )
    return list(result.scalars().all())


async def _own_credential(
    db: AsyncSession, user: User, credential_id: uuid.UUID
) -> PasskeyCredential:
    result = await db.execute(
        select(PasskeyCredential).where(
            PasskeyCredential.id == credential_id,
            PasskeyCredential.user_id == user.id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found"
        )
    return row


async def _step_up(
    db: AsyncSession, user: User, request: Request, body: PasskeyRegisterBegin
) -> None:
    """The enrolment gate: password, plus TOTP when the account has it.

    Mirrors change-password rather than TOTP setup: enrolling a passkey
    mints access that skips TOTP, so the code is demanded here even though
    enabling TOTP itself only asks for the password. A recovery code stands
    in for the TOTP code, as it does there. Failures feed the unified
    lockout, because these credentials are brute-forceable.
    """

    async def _sec(outcome: str) -> None:
        await record_security_event(
            event_type=SecurityEventType.PASSKEY_ENROLL,
            outcome=outcome,
            user_id=user.id,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )

    ensure_not_locked(user)

    if not await verify_password(body.password, user.password_hash):
        await record_login_failure(db, user)
        await _sec("password_incorrect")
        logger.warning(
            "passkey-enrol step-up failed (wrong password): user=%s ip=%s",
            user.id,
            client_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password"
        )

    if user.totp_enabled:
        if not body.totp_code:
            await _sec("totp_required")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TOTP code required",
                headers={"X-Sheaf-2FA": "required"},
            )
        secret = decrypt(user.totp_secret, aad=user_totp_secret_aad(user.id))
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


@router.get("", response_model=list[PasskeyRead])
async def list_passkeys(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rp = _relying_party()
    _refuse_api_keys(request)
    return [_to_read(row, rp) for row in await _own_credentials(db, user)]


@router.post(
    "/register/begin",
    response_model=PasskeyRegisterBeginResponse,
    dependencies=[rate_limit(5, 60, "user", fail_closed=True)],
)
async def register_begin(
    body: PasskeyRegisterBegin,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Step up, then mint the options the browser needs to create a key.

    The challenge is remembered server-side, bound to this account, for
    `complete` to consume. Nothing is written to the account here.
    """
    rp = _relying_party()
    _refuse_api_keys(request)
    await _step_up(db, user, request, body)

    existing = await _own_credentials(db, user)
    email = decrypt(user.email, aad=user_email_aad(user.id))
    system_name = await db.scalar(
        select(System.name).where(System.user_id == user.id)
    )
    start = registration_options(
        rp=rp,
        user_id=user.id,
        user_name=email,
        user_display_name=system_name or email,
        exclude_credential_ids=[row.credential_id for row in existing],
    )
    await store_registration_challenge(start.challenge, user.id)
    return PasskeyRegisterBeginResponse(options=json.loads(start.options_json))


@router.post(
    "/register/complete",
    response_model=PasskeyRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit(5, 60, "user", fail_closed=True)],
)
async def register_complete(
    body: PasskeyRegisterComplete,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify the browser's response and store the credential.

    The step-up already happened at `begin`; what ties the two together is
    the challenge, which was bound to this account when issued and is
    consumed here exactly once. A challenge issued to someone else, an
    expired one, or a replay all fail the same way.
    """
    rp = _relying_party()
    _refuse_api_keys(request)

    # Captured up front: a rollback below expires every loaded attribute on
    # `user`, and touching one afterwards would try to reload it mid-error.
    user_id = user.id
    event_ip = client_ip(request)
    event_ua = request.headers.get("user-agent")

    async def _sec(outcome: str) -> None:
        await record_security_event(
            event_type=SecurityEventType.PASSKEY_ENROLL,
            outcome=outcome,
            user_id=user_id,
            ip=event_ip,
            user_agent=event_ua,
        )

    try:
        challenge = challenge_from_credential(body.credential)
    except PasskeyCeremonyError as exc:
        await _sec(exc.reason)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail
        ) from exc

    issued_to = await take_registration_challenge(challenge)
    if issued_to != user_id:
        await _sec("challenge_invalid")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That passkey enrolment has expired or was already used. Start again.",
        )

    try:
        verified = verify_enrolment(
            credential=body.credential, expected_challenge=challenge, rp=rp
        )
    except PasskeyCeremonyError as exc:
        await _sec(exc.reason)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail
        ) from exc

    row = PasskeyCredential(
        id=uuid.uuid4(),
        user_id=user_id,
        credential_id=verified.credential_id,
        public_key=verified.public_key,
        sign_count=verified.sign_count,
        rp_id=rp.rp_id,
        transports=verified.transports,
        aaguid=verified.aaguid,
        backup_eligible=verified.backup_eligible,
        backup_state=verified.backup_state,
        nickname=(body.nickname or "").strip() or None,
        created_ip=client_ip(request),
    )
    db.add(row)
    await log_activity(
        db,
        user_id=user_id,
        action=ActivityAction.PASSKEY_ADDED,
        target_label=row.nickname,
    )
    try:
        await db.commit()
    except IntegrityError:
        # The exclude list should have stopped the browser re-enrolling a
        # key we already hold; a race or a client that ignored it lands
        # here. The unique credential id is the truth.
        await db.rollback()
        await _sec("duplicate")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That passkey is already registered on an account.",
        ) from None

    await _sec("success")
    logger.info("passkey enrolled: user=%s ip=%s", user_id, event_ip)
    await db.refresh(row)
    return _to_read(row, rp)


@router.patch(
    "/{credential_id}",
    response_model=PasskeyRead,
    dependencies=[rate_limit(10, 60, "user")],
)
async def rename_passkey(
    credential_id: uuid.UUID,
    body: PasskeyUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rp = _relying_party()
    _refuse_api_keys(request)
    row = await _own_credential(db, user, credential_id)
    row.nickname = (body.nickname or "").strip() or None
    await db.commit()
    await db.refresh(row)
    return _to_read(row, rp)


@router.delete(
    "/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[rate_limit(10, 60, "user")],
)
async def delete_passkey(
    credential_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a passkey. No password, and the last one goes too.

    Deliberately ungated: this narrows the account rather than weakening
    it, and nothing may stand between someone and reducing their exposure.
    The password remains whatever happens here, so there is no
    "last credential" case to protect.
    """
    _relying_party()
    _refuse_api_keys(request)
    row = await _own_credential(db, user, credential_id)
    await log_activity(
        db,
        user_id=user.id,
        action=ActivityAction.PASSKEY_REMOVED,
        target_label=row.nickname,
    )
    await db.delete(row)
    await db.commit()
