import secrets

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


def generate_recovery_codes(count: int = 8) -> list[str]:
    """Generate single-use recovery codes.

    Each code is 16 hex chars (64 bits of entropy). Formatted as two
    hyphen-separated groups of 8 for readability when users transcribe them.
    """
    return [
        f"{secrets.token_hex(4)}-{secrets.token_hex(4)}"
        for _ in range(count)
    ]
