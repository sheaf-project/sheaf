"""Captcha challenge issuance and verification.

Only altcha is implemented in v1 (in-process PoW, no third-party dependency).
Adding another provider means adding branches in ``issue_challenge`` /
``verify`` — the rest of the app only calls those two functions plus the
``required_for_*`` helpers.
"""

import datetime
import logging
from typing import Any

from altcha import create_challenge, verify_solution

from sheaf.config import settings

logger = logging.getLogger("sheaf.captcha")

# How long a challenge remains valid after issuance. Short enough to keep the
# replay window tight, long enough that a user solving on a slow device or
# pausing mid-form still succeeds.
CHALLENGE_TTL_SECONDS = 600

# PBKDF2 is the broadest-compatibility choice — Argon2id/Scrypt are stronger
# per CPU-second but require WASM workers, which is extra browser surface area
# for a signup-speed-bumping use case.
_ALGORITHM = "PBKDF2/SHA-256"


def required_for_signup() -> bool:
    return settings.captcha_provider == "altcha" and bool(settings.altcha_hmac_key)


def required_for_login() -> bool:
    return required_for_signup() and settings.captcha_on_login


def issue_challenge() -> dict[str, Any]:
    """Generate a new challenge payload for the client widget."""
    expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
        seconds=CHALLENGE_TTL_SECONDS
    )
    challenge = create_challenge(
        _ALGORITHM,
        cost=settings.altcha_complexity,
        hmac_secret=settings.altcha_hmac_key,
        expires_at=expires,
    )
    return challenge.to_dict()


def verify(payload: str | None) -> bool:
    """Verify a client-submitted solution payload (base64-encoded JSON)."""
    if not payload:
        return False
    try:
        result = verify_solution(payload, settings.altcha_hmac_key)
    except Exception:
        logger.exception("Captcha verification raised")
        return False
    if not result.verified:
        logger.info(
            "Captcha verification failed: expired=%s invalid_signature=%s invalid_solution=%s",
            result.expired,
            result.invalid_signature,
            result.invalid_solution,
        )
    return result.verified
