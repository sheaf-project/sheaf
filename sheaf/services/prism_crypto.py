"""PRISM1 export envelope decryption.

Prism (prismplural/prism-app) ships its data export in a custom
encrypted envelope. The on-disk format is fully documented in
prism-app's `lib/features/data_management/services/export_crypto.dart`:

```
PRISM1        (6 bytes magic)
N             (4 bytes BE uint32 — scrypt cost factor)
r             (4 bytes BE uint32 — scrypt block size)
p             (4 bytes BE uint32 — scrypt parallelization)
salt          (32 bytes, random)
nonce         (12 bytes, random — GCM standard)
json_len      (4 bytes BE uint32)
json_ct       (json_len bytes — AES-256-GCM(JSON utf8) || 16-byte GCM tag)
media_count   (4 bytes BE uint32)
--- repeated media_count times ---
id_len        (4 bytes BE uint32)
id_bytes      (id_len bytes — UTF-8 mediaId)
blob_len      (4 bytes BE uint32)
blob_bytes    (blob_len bytes — XChaCha20-Poly1305 nonce || ct+tag, carried as-is)
```

The JSON section is encrypted with a scrypt-derived AES-256-GCM key
(passphrase + salt -> 32 byte key). Each media blob is independently
encrypted with its own random 32-byte XChaCha20-Poly1305 key; the
key (`encryptionKeyB64`), content hash, and plaintext hash all live
inside the JSON entry that references that mediaId.

Magic header is PRISM1 in the spec. The prism-app importer also
accepts PRISM3 for back-compat; we mirror that on read.

Decryption is server-side: passphrase travels in an
`encrypted_credential` field on the import job (encrypted at rest
with SHEAF_ENCRYPTION_KEY, wiped at terminal state) and is fed to
scrypt inside the runner. The plaintext passphrase never lands on
disk and is never logged.
"""

from __future__ import annotations

import io
import json
import logging
import os
import struct
from base64 import b64decode
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from nacl.bindings.crypto_aead import (
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_KEYBYTES,
    crypto_aead_xchacha20poly1305_ietf_NPUBBYTES,
)

from sheaf.services.import_parsing import ImportPayloadError

logger = logging.getLogger("sheaf.imports.prism.crypto")

_MAGIC_PRISM1 = b"PRISM1"
_MAGIC_PRISM3 = b"PRISM3"
_ACCEPTED_MAGICS = (_MAGIC_PRISM1, _MAGIC_PRISM3)
_MAGIC_LEN = 6

_SALT_LEN = 32
_GCM_NONCE_LEN = 12
_GCM_TAG_LEN = 16
_KEY_LEN = 32

# scrypt bounds: paranoid about a hostile header asking us to derive a
# 64-GB-RAM key. Prism ships N=32768, r=8, p=1 (~32 MB). We accept up
# to N=2^17 which is ~128 MB so future Prism tuning still works, and
# refuse anything above that as malformed / DoS attempt rather than
# trusting the header blindly.
_SCRYPT_N_MAX = 1 << 17
_SCRYPT_R_MAX = 16
_SCRYPT_P_MAX = 4

# Cap how much JSON ciphertext we'll try to allocate. The exporter has
# a 128 MB soft limit; we accept up to 256 MB so an unusual but
# legitimate large export still parses, and reject larger as malformed.
_MAX_JSON_CT_LEN = 256 * 1024 * 1024

# Cap per-blob size + total media count so a malformed header can't
# trick us into allocating absurd buffers before the GCM tag check
# would have aborted.
_MAX_MEDIA_BLOB_LEN = 256 * 1024 * 1024
_MAX_MEDIA_COUNT = 1_000_000


@dataclass(frozen=True)
class PrismEnvelopeHeader:
    """Parsed PRISM1 header, without yet performing key derivation.

    Available before passphrase is supplied so a preview endpoint can
    surface "this is a PRISM1 file, NxRxP scrypt params, N media
    blobs" before charging the user the scrypt cost.
    """

    magic: bytes
    scrypt_n: int
    scrypt_r: int
    scrypt_p: int
    salt: bytes
    gcm_nonce: bytes
    json_ct_len: int
    media_count: int


def parse_header(blob: bytes) -> PrismEnvelopeHeader:
    """Validate magic + scrypt params + ciphertext length fields.

    Does no key derivation, no decryption. Returns the header so a
    caller can decide whether to proceed (e.g. UI preview). Raises
    `ImportPayloadError` with a user-facing message on any shape
    failure.
    """
    if len(blob) < _MAGIC_LEN + 12 + _SALT_LEN + _GCM_NONCE_LEN + 4:
        raise ImportPayloadError("file is too small to be a Prism export")
    magic = blob[:_MAGIC_LEN]
    if magic not in _ACCEPTED_MAGICS:
        raise ImportPayloadError(
            "file is not a Prism encrypted export (magic mismatch)"
        )
    cur = _MAGIC_LEN
    scrypt_n = _read_u32(blob, cur)
    cur += 4
    scrypt_r = _read_u32(blob, cur)
    cur += 4
    scrypt_p = _read_u32(blob, cur)
    cur += 4
    _validate_scrypt_params(scrypt_n, scrypt_r, scrypt_p)
    salt = blob[cur : cur + _SALT_LEN]
    cur += _SALT_LEN
    gcm_nonce = blob[cur : cur + _GCM_NONCE_LEN]
    cur += _GCM_NONCE_LEN
    json_ct_len = _read_u32(blob, cur)
    cur += 4
    if json_ct_len < _GCM_TAG_LEN:
        raise ImportPayloadError("JSON ciphertext is shorter than the GCM tag")
    if json_ct_len > _MAX_JSON_CT_LEN:
        raise ImportPayloadError(
            f"JSON ciphertext length {json_ct_len} exceeds {_MAX_JSON_CT_LEN}"
        )
    if cur + json_ct_len + 4 > len(blob):
        raise ImportPayloadError("file is truncated mid-JSON-ciphertext")
    media_count = _read_u32(blob, cur + json_ct_len)
    if media_count > _MAX_MEDIA_COUNT:
        raise ImportPayloadError(
            f"media count {media_count} exceeds {_MAX_MEDIA_COUNT}"
        )
    return PrismEnvelopeHeader(
        magic=magic,
        scrypt_n=scrypt_n,
        scrypt_r=scrypt_r,
        scrypt_p=scrypt_p,
        salt=salt,
        gcm_nonce=gcm_nonce,
        json_ct_len=json_ct_len,
        media_count=media_count,
    )


@dataclass
class DecryptedEnvelope:
    """Result of a successful envelope decryption.

    `media_blobs` keys are the export's UTF-8 `mediaId` strings; values
    are the raw XChaCha20-Poly1305 (nonce || ct+tag) bytes, carried as-is
    from the envelope. The per-blob keys live in the JSON entries that
    reference them; the caller (importer) pairs the two by mediaId.
    """

    header: PrismEnvelopeHeader
    json: dict
    media_blobs: dict[str, bytes]


def decrypt_envelope(blob: bytes, passphrase: str) -> DecryptedEnvelope:
    """Decrypt a full PRISM1 envelope with the user's passphrase.

    Raises `ImportPayloadError` for envelope shape failures and
    `ImportPayloadError` with a `wrong passphrase` message for GCM
    authentication failures — same exception type either way so the
    runner reports both as a clean job failure rather than a 500.

    The JSON ciphertext is decrypted into a Python dict. Media blobs
    are *not* decrypted here; the per-blob keys live inside the JSON
    and the importer pairs them up.
    """
    header = parse_header(blob)
    key = _derive_key(passphrase, header)

    cur = _MAGIC_LEN + 12 + _SALT_LEN + _GCM_NONCE_LEN + 4
    json_ct = blob[cur : cur + header.json_ct_len]
    cur += header.json_ct_len
    media_count_field = blob[cur : cur + 4]
    if len(media_count_field) != 4:
        raise ImportPayloadError("file is truncated before media_count")
    cur += 4

    aes = AESGCM(key)
    try:
        json_plaintext = aes.decrypt(header.gcm_nonce, json_ct, None)
    except Exception as exc:  # noqa: BLE001 — cryptography raises InvalidTag
        raise ImportPayloadError(
            "wrong passphrase or corrupted JSON section"
        ) from exc

    try:
        json_text = json_plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportPayloadError(
            f"decrypted JSON is not valid UTF-8 JSON: {exc}"
        ) from exc
    # Element-capped parse (same guard the JSON-file importers use): the
    # ciphertext length cap bounds bytes, not graph density. Raises
    # ImportPayloadError itself on bad JSON or a graph over the cap.
    from sheaf.services.import_parsing import safe_json_loads

    json_obj = safe_json_loads(json_text)
    if not isinstance(json_obj, dict):
        raise ImportPayloadError(
            "decrypted JSON must be an object at the top level"
        )

    media_blobs: dict[str, bytes] = {}
    for _ in range(header.media_count):
        if cur + 4 > len(blob):
            raise ImportPayloadError(
                "file is truncated before a media id length"
            )
        id_len = _read_u32(blob, cur)
        cur += 4
        if cur + id_len > len(blob):
            raise ImportPayloadError(
                "file is truncated before a media id payload"
            )
        try:
            media_id = blob[cur : cur + id_len].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ImportPayloadError(
                "media id is not valid UTF-8"
            ) from exc
        cur += id_len
        if cur + 4 > len(blob):
            raise ImportPayloadError(
                f"file is truncated before media blob length for {media_id!r}"
            )
        blob_len = _read_u32(blob, cur)
        cur += 4
        if blob_len > _MAX_MEDIA_BLOB_LEN:
            raise ImportPayloadError(
                f"media blob {media_id!r} length {blob_len} exceeds "
                f"{_MAX_MEDIA_BLOB_LEN}"
            )
        if cur + blob_len > len(blob):
            raise ImportPayloadError(
                f"file is truncated before media blob payload for {media_id!r}"
            )
        media_blobs[media_id] = blob[cur : cur + blob_len]
        cur += blob_len

    if cur != len(blob):
        # Excess bytes after the last media blob aren't fatal but worth
        # logging — it's a sign of a malformed exporter or attempted
        # smuggling. We don't surface to the user.
        logger.warning(
            "Prism envelope had %d trailing bytes after final media blob",
            len(blob) - cur,
        )

    return DecryptedEnvelope(
        header=header,
        json=json_obj,
        media_blobs=media_blobs,
    )


def decrypt_media_blob(blob: bytes, key_b64: str) -> bytes:
    """Decrypt one XChaCha20-Poly1305 media blob.

    `blob` layout matches prism-sync-crypto::xchacha_encrypt:
    `nonce(24) || ciphertext+poly1305_tag(>=16)`. `key_b64` is the
    standard base64 encoding of the 32-byte key (as exported in the
    JSON entry's `encryptionKeyB64`).

    Returns the decrypted plaintext. Raises `ImportPayloadError` on
    any failure (short blob, malformed key, MAC mismatch).
    """
    try:
        key = b64decode(key_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ImportPayloadError(
            "media encryption key is not valid base64"
        ) from exc
    if len(key) != crypto_aead_xchacha20poly1305_ietf_KEYBYTES:
        raise ImportPayloadError(
            f"media encryption key must be {crypto_aead_xchacha20poly1305_ietf_KEYBYTES} bytes, "
            f"got {len(key)}"
        )
    if len(blob) < crypto_aead_xchacha20poly1305_ietf_NPUBBYTES + 16:
        raise ImportPayloadError("media blob is shorter than nonce + tag")
    nonce = blob[:crypto_aead_xchacha20poly1305_ietf_NPUBBYTES]
    ct = blob[crypto_aead_xchacha20poly1305_ietf_NPUBBYTES:]
    try:
        return crypto_aead_xchacha20poly1305_ietf_decrypt(ct, None, nonce, key)
    except Exception as exc:  # noqa: BLE001
        raise ImportPayloadError(
            "media blob decryption failed (wrong key or corrupted ciphertext)"
        ) from exc


def _derive_key(passphrase: str, header: PrismEnvelopeHeader) -> bytes:
    """Run scrypt with the header's parameters to derive the AES key.

    Scrypt parameters from the header rather than constants so a
    future exporter version with different N/r/p still works. The
    `_validate_scrypt_params` cap in `parse_header` bounds the worst-
    case RAM consumption ahead of this call.
    """
    kdf = Scrypt(
        salt=header.salt,
        length=_KEY_LEN,
        n=header.scrypt_n,
        r=header.scrypt_r,
        p=header.scrypt_p,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _validate_scrypt_params(n: int, r: int, p: int) -> None:
    """Reject scrypt headers that would ask for absurd RAM.

    Scrypt memory is approximately 128 * N * r * p bytes. With the
    caps here, worst case is 128 * 2^17 * 16 * 4 = 1 GB — paranoid
    but bounded.
    """
    if n < 2 or (n & (n - 1)) != 0:
        raise ImportPayloadError(
            f"scrypt N={n} must be a power of two greater than 1"
        )
    if n > _SCRYPT_N_MAX:
        raise ImportPayloadError(
            f"scrypt N={n} exceeds maximum accepted {_SCRYPT_N_MAX}"
        )
    if r < 1 or r > _SCRYPT_R_MAX:
        raise ImportPayloadError(
            f"scrypt r={r} out of accepted range [1, {_SCRYPT_R_MAX}]"
        )
    if p < 1 or p > _SCRYPT_P_MAX:
        raise ImportPayloadError(
            f"scrypt p={p} out of accepted range [1, {_SCRYPT_P_MAX}]"
        )


def _read_u32(blob: bytes, offset: int) -> int:
    """Read a big-endian uint32 from `blob` at `offset`.

    Defensive against short blobs; raises a user-facing error rather
    than letting `struct.unpack_from` surface its own message.
    """
    if offset + 4 > len(blob):
        raise ImportPayloadError("file is truncated mid-header")
    return struct.unpack_from(">I", blob, offset)[0]


# --- Helpers for tests / synthesis ----------------------------------------


def synthesize_envelope(
    json_obj: dict,
    passphrase: str,
    media_blobs: list[tuple[str, bytes]] | None = None,
    *,
    scrypt_n: int = 1 << 14,
    scrypt_r: int = 8,
    scrypt_p: int = 1,
    salt: bytes | None = None,
    nonce: bytes | None = None,
) -> bytes:
    """Build a valid PRISM1 envelope. Used by tests + fixtures only.

    Defaults to N=16384 (half of Prism's production default) so tests
    don't pay the full ~200 ms scrypt cost per case. Real exports
    still parse fine because N is read from the header.
    """
    salt = salt or os.urandom(_SALT_LEN)
    nonce = nonce or os.urandom(_GCM_NONCE_LEN)
    media_blobs = media_blobs or []

    header_blob = (
        _MAGIC_PRISM1
        + struct.pack(">I", scrypt_n)
        + struct.pack(">I", scrypt_r)
        + struct.pack(">I", scrypt_p)
        + salt
        + nonce
    )
    kdf = Scrypt(
        salt=salt, length=_KEY_LEN, n=scrypt_n, r=scrypt_r, p=scrypt_p
    )
    key = kdf.derive(passphrase.encode("utf-8"))
    aes = AESGCM(key)
    json_ct = aes.encrypt(nonce, json.dumps(json_obj).encode("utf-8"), None)
    out = io.BytesIO()
    out.write(header_blob)
    out.write(struct.pack(">I", len(json_ct)))
    out.write(json_ct)
    out.write(struct.pack(">I", len(media_blobs)))
    for media_id, blob in media_blobs:
        id_bytes = media_id.encode("utf-8")
        out.write(struct.pack(">I", len(id_bytes)))
        out.write(id_bytes)
        out.write(struct.pack(">I", len(blob)))
        out.write(blob)
    return out.getvalue()


__all__ = [
    "DecryptedEnvelope",
    "PrismEnvelopeHeader",
    "decrypt_envelope",
    "decrypt_media_blob",
    "parse_header",
    "synthesize_envelope",
]
