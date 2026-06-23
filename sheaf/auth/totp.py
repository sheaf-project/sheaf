import secrets
import uuid
from enum import StrEnum

import pyotp


def generate_secret() -> str:
    """Generate a new TOTP secret."""
    return pyotp.random_base32()


def get_provisioning_uri(secret: str, email: str) -> str:
    """Get the otpauth:// URI for QR code generation."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name="Sheaf")


def verify_code(secret: str, code: str) -> bool:
    """Verify a TOTP code. Allows 1 window of drift."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


# A code is accepted for up to 3 timesteps (now ±1 window of drift), i.e.
# 90 seconds. The consumed-marker must outlive that whole window or a
# shoulder-surfed code could be replayed in the drift tail.
_REPLAY_TTL_SECONDS = 180


class TotpCheck(StrEnum):
    """Outcome of a single-use TOTP check.

    OK      - code valid and not previously used; now consumed.
    INVALID - code wrong (or expired) for the secret.
    REPLAY  - code is a genuine current code but has already been spent
              at some TOTP gate inside its validity window. Distinct from
              INVALID so callers can tell the user to wait for the next
              code rather than implying they typed it wrong.
    """

    OK = "ok"
    INVALID = "invalid"
    REPLAY = "replay"


# Shared user-facing text for a replayed (already-spent) code. Kept here so
# every TOTP-gated endpoint surfaces the same explanation.
REPLAY_DETAIL = (
    "That code has already been used. Wait for your authenticator app to "
    "show the next code, then try again."
)
INVALID_DETAIL = "Invalid TOTP code"


def totp_error_detail(result: TotpCheck) -> str:
    """Map a non-OK TotpCheck to the message shown to the user."""
    return REPLAY_DETAIL if result is TotpCheck.REPLAY else INVALID_DETAIL


async def check_code_once(user_id: uuid.UUID, secret: str, code: str) -> TotpCheck:
    """Verify a TOTP code AND consume it, distinguishing replays.

    TOTP codes are valid for ~90s (30s step, ±1 window of drift), so a
    code observed in transit or over a shoulder can be replayed at any
    other TOTP-gated endpoint inside that window. Marking each accepted
    code as used in Redis (SET NX, TTL outliving the validity window)
    makes every code single-use across the whole API.

    Returns TotpCheck.OK when the code was valid and freshly consumed,
    REPLAY when it was a real current code already spent, INVALID
    otherwise. Use `verify_code_once` for the plain bool when the caller
    doesn't care which kind of failure it was.

    Side effect on failure paths: a code consumed here is burnt even if
    the surrounding request later fails for an unrelated reason; the
    user waits one 30s step for a fresh code. Fails closed: if Redis is
    unreachable the call raises rather than silently skipping the guard.
    """
    if not verify_code(secret, code):
        return TotpCheck.INVALID
    from sheaf.auth.sessions import get_redis

    r = await get_redis()
    stored = await r.set(
        f"sheaf:totp_used:{user_id}:{code}", "1", nx=True, ex=_REPLAY_TTL_SECONDS
    )
    return TotpCheck.OK if stored else TotpCheck.REPLAY


async def verify_code_once(user_id: uuid.UUID, secret: str, code: str) -> bool:
    """Verify and consume a TOTP code, rejecting replays.

    Thin bool wrapper over `check_code_once` for callers that don't need
    to distinguish a wrong code from a replayed one. Use this everywhere
    a *user's enrolled* TOTP code authorises something and the failure
    message is uniform; reach for `check_code_once` when you want to tell
    the user a replayed code apart from a wrong one.
    """
    return (await check_code_once(user_id, secret, code)) is TotpCheck.OK


def generate_recovery_codes(count: int = 8) -> list[str]:
    """Generate single-use recovery codes.

    Each code is 16 hex chars (64 bits of entropy). Formatted as two
    hyphen-separated groups of 8 for readability when users transcribe them.
    """
    return [
        f"{secrets.token_hex(4)}-{secrets.token_hex(4)}"
        for _ in range(count)
    ]
