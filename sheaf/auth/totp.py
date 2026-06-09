import secrets
import uuid

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


async def verify_code_once(user_id: uuid.UUID, secret: str, code: str) -> bool:
    """Verify a TOTP code AND consume it, rejecting replays.

    TOTP codes are valid for ~90s (30s step, ±1 window of drift), so a
    code observed in transit or over a shoulder can be replayed at any
    other TOTP-gated endpoint inside that window. Marking each accepted
    code as used in Redis (SET NX, TTL outliving the validity window)
    makes every code single-use across the whole API.

    Use this everywhere a *user's enrolled* TOTP code authorises
    something. Plain `verify_code` remains for non-consuming checks.

    Side effect on failure paths: a code consumed here is burnt even if
    the surrounding request later fails for an unrelated reason; the
    user waits one 30s step for a fresh code. Fails closed — if Redis is
    unreachable the call raises rather than silently skipping the guard.
    """
    if not verify_code(secret, code):
        return False
    from sheaf.auth.sessions import get_redis

    r = await get_redis()
    return bool(
        await r.set(f"sheaf:totp_used:{user_id}:{code}", "1", nx=True, ex=_REPLAY_TTL_SECONDS)
    )


def generate_recovery_codes(count: int = 8) -> list[str]:
    """Generate single-use recovery codes.

    Each code is 16 hex chars (64 bits of entropy). Formatted as two
    hyphen-separated groups of 8 for readability when users transcribe them.
    """
    return [
        f"{secrets.token_hex(4)}-{secrets.token_hex(4)}"
        for _ in range(count)
    ]
