"""Passkeys: `/v1/auth/passkeys`.

Two halves. Enrolment and management (authenticated) is where an account
gains, lists, renames and removes credentials. Sign-in (unauthenticated) is
where a credential gets someone into the account. The rules, from the
passkey design:

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
* Sign-in takes no email and no body at `begin`: the credential is
  discoverable, so there is nothing to enumerate. It honours an existing
  lockout and the suspended/banned refusals exactly as login does, reaches a
  session only through login's own success path, and never feeds the
  lockout counter: a signature is not brute-forceable, and counting failures
  would let anyone holding a credential id lock its owner out of their
  password.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# Borrowed from the login module rather than copied: the recovery-code check
# is the one login and change-password accept, the account-standing refusal
# is the one login applies, and `_finalise_login` is the only way a session
# gets minted. A passkey must reach the account by exactly login's code.
from sheaf.api.v1.auth import (
    _account_standing_refusal,
    _check_recovery_code,
    _finalise_login,
)
from sheaf.auth.dependencies import get_current_user
from sheaf.auth.lockout import ensure_not_locked, record_login_failure
from sheaf.auth.passkey_ceremony import (
    PasskeyCeremonyError,
    assertion_identity,
    authentication_options,
    challenge_from_credential,
    registration_options,
    store_registration_challenge,
    store_signin_challenge,
    take_registration_challenge,
    take_signin_challenge,
    verify_assertion,
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
from sheaf.observability.metrics import LoginOutcome, auth_logins_total
from sheaf.request import client_ip
from sheaf.schemas.passkey import (
    PasskeyRead,
    PasskeyRegisterBegin,
    PasskeyRegisterBeginResponse,
    PasskeyRegisterComplete,
    PasskeySignInBeginResponse,
    PasskeySignInComplete,
    PasskeyUpdate,
)
from sheaf.schemas.user import TokenResponse
from sheaf.services import captcha
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


# --- sign-in -------------------------------------------------------------------

_SIGNIN_FAILED_DETAIL = "Passkey sign-in failed. Try again, or sign in with your password."
# Password login's own per-IP limits. The begin call is a Redis write, the
# complete call one signature check, so there is no CPU to protect here; the
# limits exist so the two surfaces are abused at the same rate.
_SIGNIN_LIMITS = [
    rate_limit(10, 60, fail_closed=True),
    rate_limit(30, 3600, fail_closed=True),
]


@router.post(
    "/sign-in/begin",
    response_model=PasskeySignInBeginResponse,
    dependencies=_SIGNIN_LIMITS,
)
async def sign_in_begin():
    """Mint the options for a discoverable sign-in. No body, no account.

    Deliberately takes nothing: the browser offers whichever credentials it
    holds for this RP and the user picks one, so there is no email to type
    and no "which credentials does this address have" question for the
    server to answer. That question is a user-enumeration oracle, and the
    cleanest mitigation is to never ask it.
    """
    rp = _relying_party()
    start = authentication_options(rp=rp)
    await store_signin_challenge(start.challenge)
    return PasskeySignInBeginResponse(options=json.loads(start.options_json))


@router.post(
    "/sign-in/complete",
    response_model=TokenResponse,
    dependencies=_SIGNIN_LIMITS,
)
async def sign_in_complete(
    body: PasskeySignInComplete,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Verify an assertion and sign the credential's owner in.

    The gates mirror login's, in login's order where the two share a step:
    captcha where configured, the lockout check honoured (never incremented),
    the signature, then the suspended/banned refusal, and finally the one
    shared success path. Every refusal that is about the credential answers
    with the same generic 401, so a caller holding a guessed or stale
    credential id learns nothing from the shape of the response; the
    distinct outcomes exist for the metric and the security log.
    """
    rp = _relying_party()
    event_ip = client_ip(request)
    event_ua = request.headers.get("user-agent")

    async def _refuse(
        outcome: LoginOutcome,
        user_id: uuid.UUID | None,
        status_code: int,
        detail: str,
    ) -> None:
        auth_logins_total.labels(outcome=outcome).inc()
        await record_security_event(
            event_type=SecurityEventType.LOGIN,
            outcome=outcome,
            user_id=user_id,
            ip=event_ip,
            user_agent=event_ua,
            detail={"credential": "passkey"},
        )
        raise HTTPException(status_code=status_code, detail=detail)

    if captcha.required_for_login() and not captcha.verify(body.captcha):
        await _refuse(
            "captcha_failed",
            None,
            status.HTTP_400_BAD_REQUEST,
            "Captcha verification failed",
        )

    try:
        identity = assertion_identity(body.credential)
    except PasskeyCeremonyError as exc:
        await _refuse("passkey_invalid", None, status.HTTP_400_BAD_REQUEST, exc.detail)

    if not await take_signin_challenge(identity.challenge):
        await _refuse(
            "passkey_invalid",
            None,
            status.HTTP_400_BAD_REQUEST,
            "That sign-in has expired or was already used. Try again.",
        )

    row = await db.scalar(
        select(PasskeyCredential).where(
            PasskeyCredential.credential_id == identity.credential_id
        )
    )
    user = await db.get(User, row.user_id) if row is not None else None
    if row is None or user is None:
        await _refuse(
            "passkey_unknown_credential",
            None,
            status.HTTP_401_UNAUTHORIZED,
            _SIGNIN_FAILED_DETAIL,
        )

    # Honour an existing lock, as login does before it touches the
    # credential. Nothing on this path ever creates one.
    try:
        ensure_not_locked(user)
    except HTTPException:
        auth_logins_total.labels(outcome="locked").inc()
        await record_security_event(
            event_type=SecurityEventType.LOGIN,
            outcome="locked",
            user_id=user.id,
            ip=event_ip,
            user_agent=event_ua,
            detail={"credential": "passkey"},
        )
        raise

    if row.rp_id != rp.rp_id:
        # A credential from before a domain move. A real browser never
        # offers one of these for the current RP ID, so reaching here means
        # something other than a browser; verification would fail anyway on
        # the RP hash. Distinct label, same generic answer.
        await _refuse(
            "passkey_rp_mismatch",
            user.id,
            status.HTTP_401_UNAUTHORIZED,
            _SIGNIN_FAILED_DETAIL,
        )

    try:
        verified = verify_assertion(
            credential=body.credential,
            expected_challenge=identity.challenge,
            rp=rp,
            public_key=row.public_key,
            stored_sign_count=row.sign_count,
        )
    except PasskeyCeremonyError:
        await _refuse(
            "passkey_invalid",
            user.id,
            status.HTTP_401_UNAUTHORIZED,
            _SIGNIN_FAILED_DETAIL,
        )

    refusal = _account_standing_refusal(user)
    if refusal is not None:
        standing_outcome, standing_exc = refusal
        auth_logins_total.labels(outcome=standing_outcome).inc()
        await record_security_event(
            event_type=SecurityEventType.LOGIN,
            outcome=standing_outcome,
            user_id=user.id,
            ip=event_ip,
            user_agent=event_ua,
            detail={"credential": "passkey"},
        )
        raise standing_exc

    if verified.sign_count_regressed:
        # A hardware key whose counter went backwards may have been cloned.
        # Logged for the operator and the account's own security timeline,
        # not fatal: refusing the legitimate owner on a heuristic that synced
        # passkeys trip by design is the wrong trade. See verify_assertion.
        logger.warning(
            "passkey sign counter regressed: user=%s credential=%s stored=%s received=%s ip=%s",
            user.id,
            row.id,
            row.sign_count,
            verified.new_sign_count,
            event_ip,
        )
        await record_security_event(
            event_type=SecurityEventType.LOGIN,
            outcome="passkey_counter_regressed",
            user_id=user.id,
            ip=event_ip,
            user_agent=event_ua,
            detail={
                "credential": "passkey",
                "passkey_id": str(row.id),
                "stored_sign_count": row.sign_count,
                "received_sign_count": verified.new_sign_count,
            },
        )

    # Rides the commit inside _finalise_login, so a Redis failure that rolls
    # the session back rolls the usage stamp back with it.
    row.sign_count = verified.new_sign_count
    row.backup_state = verified.backup_state
    row.last_used_at = datetime.now(UTC)
    row.last_used_ip = event_ip

    return await _finalise_login(db, user, request, response, outcome="passkey")
