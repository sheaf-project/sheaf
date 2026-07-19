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


@pytest.fixture(autouse=True)
def _write_v2_on(monkeypatch):
    """Exercise the steady-state v2 write path by default.

    The production write gate (`field_encryption_write_v2`) defaults off so
    the v2-capable release is a rollback-safe bridge; that staging is a
    deployment concern, verified in the gate-specific tests below. Every
    other test here is about the crypto layer's v2 behaviour, so it assumes
    the enabled state. monkeypatch restores the default at teardown.
    """
    monkeypatch.setattr(crypto.settings, "field_encryption_write_v2", True)


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
# v1 cutoff (FIELD_ENCRYPTION_ACCEPT_V1=false)
# ---------------------------------------------------------------------------

def test_v1_rejected_when_accept_v1_disabled(monkeypatch):
    # The dual-read window is also the downgrade-attack window: a DB writer
    # can overwrite a v2 cell with a preserved v1 ciphertext and the aad is
    # never consulted. The cutoff closes it: v1 tokens fail closed.
    token = crypto.encrypt("legacy value")
    monkeypatch.setattr(crypto.settings, "field_encryption_accept_v1", False)
    with pytest.raises(
        nacl.exceptions.CryptoError, match="legacy v1 ciphertext rejected"
    ):
        crypto.decrypt(token)


def test_v2_still_reads_when_accept_v1_disabled(monkeypatch):
    token = crypto.encrypt("bound", aad=_AAD)
    monkeypatch.setattr(crypto.settings, "field_encryption_accept_v1", False)
    assert crypto.decrypt(token, aad=_AAD) == "bound"


def test_v1_encrypt_rejected_when_accept_v1_disabled(monkeypatch):
    # Writing a v1 token the same instance can no longer read back would be
    # self-inflicted data loss, so a no-aad encrypt fails fast instead.
    monkeypatch.setattr(crypto.settings, "field_encryption_accept_v1", False)
    with pytest.raises(ValueError, match="v1 encryption disabled"):
        crypto.encrypt("new value")


def test_v2_encrypt_still_works_when_accept_v1_disabled(monkeypatch):
    monkeypatch.setattr(crypto.settings, "field_encryption_accept_v1", False)
    token = crypto.encrypt("bound", aad=_AAD)
    assert token.startswith("v2:")
    assert crypto.decrypt(token, aad=_AAD) == "bound"


# ---------------------------------------------------------------------------
# write gate (FIELD_ENCRYPTION_WRITE_V2)
# ---------------------------------------------------------------------------

def test_write_gate_off_writes_v1_even_with_aad(monkeypatch):
    # The bridge release: call sites already pass an aad, but until the write
    # gate flips, writes stay v1 so a rollback target that only reads v1 is
    # safe. The aad is accepted and ignored for the write.
    monkeypatch.setattr(crypto.settings, "field_encryption_write_v2", False)
    token = crypto.encrypt("bridge value", aad=_AAD)
    assert not token.startswith("v2:")
    # Still readable, and a converted call site reading it back passes its
    # aad, which the v1 path ignores.
    assert crypto.decrypt(token, aad=_AAD) == "bridge value"


def test_write_gate_on_writes_v2(monkeypatch):
    monkeypatch.setattr(crypto.settings, "field_encryption_write_v2", True)
    token = crypto.encrypt("gated value", aad=_AAD)
    assert token.startswith("v2:")


# ---------------------------------------------------------------------------
# v1 known-answer (frozen fixture)
# ---------------------------------------------------------------------------

# A v1 token FROZEN at generation time under a fixed key, so a serialization
# regression in the legacy path (key derivation, base64 variant, nonce/ct
# layout) cannot slip through while every round-trip test still passes.
# Generated once in a fresh process (so the cached boxes derived from the
# fixed key) via:
#
#   SHEAF_ENCRYPTION_KEY='frozen-v1-known-answer-key-2026-do-not-use-in-prod' \
#     uv run python -c \
#     "from sheaf import crypto; print(crypto.encrypt('frozen-v1-known-answer-plaintext'))"
_KA_KEY = "frozen-v1-known-answer-key-2026-do-not-use-in-prod"
_KA_PLAINTEXT = "frozen-v1-known-answer-plaintext"
_KA_V1_TOKEN = (
    "VWlVgmED6JofbwhFfLwBhYIO72I0WIfoTX5q6JMIEiBJgfrG2qpEXVn8"
    "-mJNJiZgCyJguqvgoJy5OcArBAzlQidkgYAqB3h8"
)


@pytest.fixture
def _known_answer_key(monkeypatch):
    """Point crypto at the frozen key and drop the cached boxes.

    `get_encryption_key()` returns `settings.sheaf_encryption_key.encode()`
    when the setting is non-empty, so patching the attribute is enough - but
    the module caches its SecretBox/Aead in `_box` / `_aead_box`, which must
    be cleared or the boxes keep the previously derived key. monkeypatch
    restores both the setting and the original cached box objects at
    teardown, so the other tests keep their cached ephemeral key untouched.
    """
    monkeypatch.setattr(crypto.settings, "sheaf_encryption_key", _KA_KEY)
    monkeypatch.setattr(crypto, "_box", None)
    monkeypatch.setattr(crypto, "_aead_box", None)


def test_v1_known_answer_frozen_token(_known_answer_key):
    assert not _KA_V1_TOKEN.startswith("v2:")
    assert crypto.decrypt(_KA_V1_TOKEN) == _KA_PLAINTEXT


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
