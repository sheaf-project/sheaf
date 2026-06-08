"""Unit tests for the PRISM1 export envelope crypto layer.

No HTTP stack, no DB — these exercise `sheaf/services/prism_crypto.py`
directly against synthesised envelopes. The envelope format is fully
spec-defined upstream (prism-app/lib/features/data_management/services/
export_crypto.dart), so the tests pin our reading of the spec rather
than testing against a real export. A separate integration test that
runs against an actual Prism export lands in the entity-importer PR.
"""

from __future__ import annotations

import os
import struct
from base64 import b64encode

import pytest
from nacl.bindings.crypto_aead import (
    crypto_aead_xchacha20poly1305_ietf_encrypt as _xchacha_encrypt,
)

from sheaf.services.import_parsing import ImportPayloadError
from sheaf.services.prism_crypto import (
    decrypt_envelope,
    decrypt_media_blob,
    parse_header,
    synthesize_envelope,
)


def _make_media_blob(plaintext: bytes) -> tuple[bytes, str]:
    """Return (blob, base64-key) for a freshly-encrypted XChaCha20 blob."""
    key = os.urandom(32)
    nonce = os.urandom(24)
    ct = _xchacha_encrypt(plaintext, None, nonce, key)
    return nonce + ct, b64encode(key).decode()


# --- Round-trip happy path -------------------------------------------------


def test_synthesize_and_decrypt_round_trip():
    blob, key_b64 = _make_media_blob(b"hello media")
    env = synthesize_envelope(
        {"formatVersion": "1.0", "headmates": [{"id": "a", "name": "Alpha"}]},
        "correct horse battery staple",
        media_blobs=[("media-id-1", blob)],
    )
    decrypted = decrypt_envelope(env, "correct horse battery staple")
    assert decrypted.json["formatVersion"] == "1.0"
    assert decrypted.json["headmates"][0]["name"] == "Alpha"
    assert set(decrypted.media_blobs) == {"media-id-1"}
    assert decrypt_media_blob(decrypted.media_blobs["media-id-1"], key_b64) == b"hello media"


def test_parse_header_returns_scrypt_params_without_passphrase():
    env = synthesize_envelope(
        {"formatVersion": "1.0"},
        "pw",
        scrypt_n=16384,
        scrypt_r=8,
        scrypt_p=1,
    )
    h = parse_header(env)
    assert h.magic == b"PRISM1"
    assert h.scrypt_n == 16384
    assert h.scrypt_r == 8
    assert h.scrypt_p == 1
    assert h.media_count == 0


def test_decrypt_envelope_with_zero_media_blobs():
    env = synthesize_envelope({"formatVersion": "1.0"}, "pw")
    decrypted = decrypt_envelope(env, "pw")
    assert decrypted.media_blobs == {}
    assert decrypted.json == {"formatVersion": "1.0"}


# --- Wrong passphrase ------------------------------------------------------


def test_wrong_passphrase_is_classified_error():
    env = synthesize_envelope({"formatVersion": "1.0"}, "right")
    with pytest.raises(ImportPayloadError, match="wrong passphrase"):
        decrypt_envelope(env, "wrong")


# --- Malformed envelopes ---------------------------------------------------


def test_rejects_non_prism_magic():
    junk = b"NOTPRISM" + b"\x00" * 200
    with pytest.raises(ImportPayloadError, match="not a Prism encrypted export"):
        parse_header(junk)


def test_rejects_too_small_blob():
    with pytest.raises(ImportPayloadError, match="too small"):
        parse_header(b"PRISM1\x00\x00")


def test_rejects_truncated_json_ciphertext():
    env = synthesize_envelope({"formatVersion": "1.0"}, "pw")
    # Truncate inside the json ciphertext region.
    truncated = env[:-len(env) // 2]
    with pytest.raises(ImportPayloadError):
        parse_header(truncated)


def test_rejects_truncated_media_section():
    blob, _ = _make_media_blob(b"x" * 8)
    env = synthesize_envelope({"formatVersion": "1.0"}, "pw", media_blobs=[("m1", blob)])
    # Lop off the tail end of the media payload after the header parses fine.
    truncated = env[:-5]
    with pytest.raises(ImportPayloadError, match="truncated"):
        decrypt_envelope(truncated, "pw")


# --- scrypt parameter caps -------------------------------------------------


def test_scrypt_n_must_be_power_of_two():
    env = bytearray(synthesize_envelope({"formatVersion": "1.0"}, "pw"))
    # Overwrite the N field (offset 6) with a non-power-of-two value.
    struct.pack_into(">I", env, 6, 12345)
    with pytest.raises(ImportPayloadError, match="power of two"):
        parse_header(bytes(env))


def test_scrypt_n_above_cap_is_rejected():
    env = bytearray(synthesize_envelope({"formatVersion": "1.0"}, "pw"))
    # Cap is 2^17; ask for 2^20 (~1 GB RAM if r=8,p=1).
    struct.pack_into(">I", env, 6, 1 << 20)
    with pytest.raises(ImportPayloadError, match="exceeds maximum"):
        parse_header(bytes(env))


def test_scrypt_r_above_cap_is_rejected():
    env = bytearray(synthesize_envelope({"formatVersion": "1.0"}, "pw"))
    struct.pack_into(">I", env, 10, 256)  # r field offset = magic(6) + N(4)
    with pytest.raises(ImportPayloadError, match="r="):
        parse_header(bytes(env))


def test_scrypt_p_above_cap_is_rejected():
    env = bytearray(synthesize_envelope({"formatVersion": "1.0"}, "pw"))
    struct.pack_into(">I", env, 14, 16)  # p field offset = magic(6) + N(4) + r(4)
    with pytest.raises(ImportPayloadError, match="p="):
        parse_header(bytes(env))


# --- Media blob decryption -------------------------------------------------


def test_media_blob_decrypt_with_correct_key():
    blob, key_b64 = _make_media_blob(b"some bytes")
    assert decrypt_media_blob(blob, key_b64) == b"some bytes"


def test_media_blob_decrypt_with_wrong_key_raises():
    blob, _ = _make_media_blob(b"some bytes")
    other_key = b64encode(os.urandom(32)).decode()
    with pytest.raises(ImportPayloadError, match="decryption failed"):
        decrypt_media_blob(blob, other_key)


def test_media_blob_decrypt_rejects_short_key():
    blob, _ = _make_media_blob(b"x")
    too_short = b64encode(b"only 16 bytes!!!").decode()
    with pytest.raises(ImportPayloadError, match="must be 32 bytes"):
        decrypt_media_blob(blob, too_short)


def test_media_blob_decrypt_rejects_bad_base64():
    blob, _ = _make_media_blob(b"x")
    with pytest.raises(ImportPayloadError, match="not valid base64"):
        decrypt_media_blob(blob, "***not-base64***")


def test_media_blob_decrypt_rejects_truncated_blob():
    _, key_b64 = _make_media_blob(b"x")
    with pytest.raises(ImportPayloadError, match="shorter than nonce"):
        decrypt_media_blob(b"too short", key_b64)


# --- Prism3 back-compat magic ---------------------------------------------


def test_prism3_magic_is_accepted_on_read():
    env = bytearray(synthesize_envelope({"formatVersion": "1.0"}, "pw"))
    env[:6] = b"PRISM3"
    # Header parse should accept PRISM3 magic; full decryption fails GCM
    # because we used the PRISM1 KDF salt/nonce path to synthesize. The
    # header gate is the contract we care about here.
    h = parse_header(bytes(env))
    assert h.magic == b"PRISM3"
