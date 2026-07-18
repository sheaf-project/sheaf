"""Headless unit tests for field encryption and its v1/v2 formats.

No DB / docker stack needed - these exercise `sheaf.crypto` directly. The
module derives its key from `sheaf.config.settings.get_encryption_key()`,
which auto-generates an ephemeral key when SHEAF_ENCRYPTION_KEY is unset, so
these round-trip tests are independent of which key is active.
"""

from __future__ import annotations

import uuid

import nacl.exceptions
import pytest

from sheaf import crypto

_PK = uuid.uuid4()
_AAD = crypto.field_aad("users", "email", _PK)


# ---------------------------------------------------------------------------
# field_aad
# ---------------------------------------------------------------------------

def test_field_aad_format_exact():
    assert crypto.field_aad("users", "email", _PK) == (
        f"sheaf-fe-v2|users|email|{_PK}".encode()
    )


def test_field_aad_differs_per_component():
    base = crypto.field_aad("users", "email", _PK)
    assert crypto.field_aad("systems", "email", _PK) != base  # table
    assert crypto.field_aad("users", "phone", _PK) != base     # column
    assert crypto.field_aad("users", "email", uuid.uuid4()) != base  # pk


# ---------------------------------------------------------------------------
# v1 (legacy, no aad)
# ---------------------------------------------------------------------------

def test_v1_roundtrip_no_prefix():
    token = crypto.encrypt("hello world")
    assert not token.startswith("v2:")
    assert crypto.decrypt(token) == "hello world"


def test_v1_empty_string_roundtrip():
    token = crypto.encrypt("")
    assert not token.startswith("v2:")
    assert crypto.decrypt(token) == ""


def test_v1_token_ignores_passed_aad():
    # Dual-read window: a converted call site passes an aad but must still be
    # able to read old v1 rows, which carry no associated data.
    token = crypto.encrypt("legacy value")
    assert crypto.decrypt(token, aad=_AAD) == "legacy value"


# ---------------------------------------------------------------------------
# v2 (AAD-bound)
# ---------------------------------------------------------------------------

def test_v2_roundtrip_has_prefix():
    token = crypto.encrypt("hello world", aad=_AAD)
    assert token.startswith("v2:")
    assert crypto.decrypt(token, aad=_AAD) == "hello world"


def test_v2_empty_string_roundtrip():
    token = crypto.encrypt("", aad=_AAD)
    assert token.startswith("v2:")
    assert crypto.decrypt(token, aad=_AAD) == ""


def test_v2_wrong_table_raises():
    token = crypto.encrypt("secret", aad=_AAD)
    wrong = crypto.field_aad("systems", "email", _PK)
    with pytest.raises(nacl.exceptions.CryptoError):
        crypto.decrypt(token, aad=wrong)


def test_v2_wrong_column_raises():
    token = crypto.encrypt("secret", aad=_AAD)
    wrong = crypto.field_aad("users", "phone", _PK)
    with pytest.raises(nacl.exceptions.CryptoError):
        crypto.decrypt(token, aad=wrong)


def test_v2_wrong_pk_raises():
    token = crypto.encrypt("secret", aad=_AAD)
    wrong = crypto.field_aad("users", "email", uuid.uuid4())
    with pytest.raises(nacl.exceptions.CryptoError):
        crypto.decrypt(token, aad=wrong)


def test_v2_decrypt_without_aad_raises_valueerror():
    # A v2 read with no context is a programming error and must NOT fall
    # through to a no-AAD path.
    token = crypto.encrypt("secret", aad=_AAD)
    with pytest.raises(ValueError, match="v2 ciphertext requires aad"):
        crypto.decrypt(token)


def test_v2_tampered_body_raises():
    import base64

    token = crypto.encrypt("secret", aad=_AAD)
    raw = bytearray(base64.urlsafe_b64decode(token[len("v2:"):]))
    # Flip a byte inside the ciphertext/tag region (past the 24-byte nonce)
    # to corrupt authentication without disturbing base64 padding.
    raw[-1] ^= 0x01
    tampered = "v2:" + base64.urlsafe_b64encode(bytes(raw)).decode()
    with pytest.raises(nacl.exceptions.CryptoError):
        crypto.decrypt(tampered, aad=_AAD)


# ---------------------------------------------------------------------------
# decrypt_field passthrough
# ---------------------------------------------------------------------------

def test_decrypt_field_passes_aad_through_success():
    token = crypto.encrypt("via field", aad=_AAD)
    assert crypto.decrypt_field(token, "email", aad=_AAD) == "via field"


def test_decrypt_field_v1_still_works():
    token = crypto.encrypt("legacy")
    assert crypto.decrypt_field(token, "email") == "legacy"


def test_decrypt_field_raises_on_wrong_aad():
    token = crypto.encrypt("secret", aad=_AAD)
    wrong = crypto.field_aad("users", "email", uuid.uuid4())
    with pytest.raises(nacl.exceptions.CryptoError):
        crypto.decrypt_field(token, "email", aad=wrong)
