"""Application-level field encryption and keyed lookup hashes.

Two on-disk ciphertext formats coexist, distinguished by a leading `v2:`
tag on the token (base64url never contains `:`, so the prefix is an
unambiguous discriminator):

- Legacy v1 (unprefixed): `nacl.secret.SecretBox` = XSalsa20-Poly1305,
  no associated data. Format `base64url(nonce||ciphertext||tag)`, 24-byte
  nonce. The Poly1305 tag authenticates the bytes but not their location,
  so a DB-write attacker can lift any v1 ciphertext into any other
  encrypted cell and it still decrypts. Read-only path now; new writes on
  converted call sites use v2.
- v2 (`v2:` prefix): `nacl.secret.Aead` = XChaCha20-Poly1305-IETF with
  associated data. Format `"v2:" + base64url(nonce||ciphertext||tag)`,
  24-byte nonce. The associated data binds each ciphertext to the one cell
  it belongs in: `sheaf-fe-v2|<table>|<column>|<pk>` (see `field_aad`). A
  ciphertext relocated to a different row or column now fails to decrypt
  (nacl CryptoError) instead of silently returning the wrong plaintext, so
  a DB-write relocation attack fails closed.

Both formats derive the same 256-bit key: `sha256(SHEAF_ENCRYPTION_KEY)`.

Callers opt into v2 by passing `aad=field_aad(...)` to `encrypt`/`decrypt`.
Until every call site is converted, `aad=None` preserves byte-for-byte v1
behaviour; a v1 token still decrypts even if an (ignored) aad is passed,
which is the dual-read window converted call sites rely on to read old
rows.

This module also owns two keyed hashes that are required for the app to
function:

- ``blind_index``: keyed HMAC-SHA-256 used to look up encrypted rows by
  plaintext (e.g. find a user by email at login). Keyed off the encryption
  key, so losing SHEAF_ENCRYPTION_KEY means nobody can log in.
- ``hash_mail_token``: keyed HMAC-SHA-256 used to store the DB-side form of
  password-reset and email-verification tokens. Keyed off JWT_SECRET_KEY.
"""

import base64
import hashlib
import hmac
import os

import nacl.secret

from sheaf.config import settings

# Marker prefix for v2 (AEAD-with-associated-data) tokens. base64url never
# emits ':', so its presence at the front is an unambiguous format tag.
_V2_PREFIX = "v2:"

_box: nacl.secret.SecretBox | None = None
_aead_box: nacl.secret.Aead | None = None


def _get_box() -> nacl.secret.SecretBox:
    global _box
    if _box is None:
        raw_key = settings.get_encryption_key()
        # Derive 32-byte key uniformly from whatever the user provided
        derived = hashlib.sha256(raw_key).digest()
        _box = nacl.secret.SecretBox(derived)
    return _box


def _get_aead_box() -> nacl.secret.Aead:
    """Lazily build the v2 AEAD box off the same derived key as `_get_box`.

    `nacl.secret.Aead` wraps crypto_aead_xchacha20poly1305_ietf and, like
    SecretBox, takes a 24-byte nonce and serialises to nonce||ct||tag.
    """
    global _aead_box
    if _aead_box is None:
        raw_key = settings.get_encryption_key()
        derived = hashlib.sha256(raw_key).digest()
        _aead_box = nacl.secret.Aead(derived)
    return _aead_box


def field_aad(table: str, column: str, pk) -> bytes:
    """Canonical associated data binding one encrypted cell in place.

    `table` and `column` are the physical Postgres identifiers, which are
    `[a-z0-9_]` only, so `|` can never appear inside them and the fields are
    unambiguous. `pk` is the row's UUID, stringified canonically via `str()`.
    The fixed `sheaf-fe-v2` label gives domain separation from any other AEAD
    use of this key and leaves room for a future v3.
    """
    return f"sheaf-fe-v2|{table}|{column}|{pk}".encode()


def encrypt(plaintext: str, *, aad: bytes | None = None) -> str:
    """Encrypt a string. Returns a URL-safe base64 token.

    With `aad=None` this is the legacy v1 path: SecretBox, unprefixed
    base64url, byte-for-byte identical to the pre-AAD behaviour. This path
    stays until every call site passes an aad, at which point aad should
    become mandatory and the v1 branch can go.

    With an `aad`, this is v2: the Aead box under a fresh 24-byte nonce with
    the aad as associated data, emitted as `"v2:" + base64url(nonce||ct||tag)`.
    Pass `field_aad(table, column, pk)` so the ciphertext is bound to its cell.
    """
    if aad is None:
        box = _get_box()
        nonce = os.urandom(nacl.secret.SecretBox.NONCE_SIZE)
        ct = box.encrypt(plaintext.encode(), nonce)
        return base64.urlsafe_b64encode(ct).decode()

    box = _get_aead_box()
    nonce = os.urandom(nacl.secret.Aead.NONCE_SIZE)
    # Aead.encrypt(plaintext, aad, nonce) returns an EncryptedMessage whose
    # bytes are nonce||ct||tag when a nonce is supplied, mirroring SecretBox.
    ct = box.encrypt(plaintext.encode(), aad, nonce)
    return _V2_PREFIX + base64.urlsafe_b64encode(ct).decode()


def decrypt(token: str, *, aad: bytes | None = None) -> str:
    """Decrypt a token back to plaintext, dispatching on the format tag.

    A `v2:` token is decrypted with the Aead box and requires an `aad`: a v2
    read with no context is a programming error, so it raises rather than
    falling through to any no-AAD path. A v1 (unprefixed) token uses the
    legacy SecretBox path; any `aad` passed for it is ignored, which is what
    lets a converted call site read old v1 rows during the dual-read window.
    """
    if token.startswith(_V2_PREFIX):
        if aad is None:
            raise ValueError("v2 ciphertext requires aad")
        box = _get_aead_box()
        raw = base64.urlsafe_b64decode(token[len(_V2_PREFIX):])
        plaintext = box.decrypt(raw, aad).decode()
        version = "v2"
    else:
        box = _get_box()
        raw = base64.urlsafe_b64decode(token)
        plaintext = box.decrypt(raw).decode()
        version = "v1"

    # Import here to avoid an import cycle (crypto is imported very early in
    # the bootstrap path, before observability is ready).
    from sheaf.observability.metrics import field_decrypts_total
    field_decrypts_total.labels(version=version).inc()
    return plaintext


def decrypt_field(token: str, field: str, *, aad: bytes | None = None) -> str:
    """`decrypt()` wrapper that bumps the decrypt-failure metric on error.

    `field` labels the failure so dashboards can answer "which field is
    drifting?" - should always be zero; non-zero indicates encryption-key
    drift or storage corruption. `aad` is passed straight through to
    `decrypt`. Re-raises the original exception so callers behave identically
    to plain `decrypt()`.

    An AAD mismatch (a relocated v2 ciphertext) surfaces as the same nacl
    CryptoError as key drift, so it cannot be told apart at this layer and
    gets no distinct label; this per-field failure counter stays the alert
    signal for both.
    """
    try:
        return decrypt(token, aad=aad)
    except Exception:
        # Import here to avoid an import cycle (crypto is imported very
        # early in the bootstrap path before observability is ready).
        from sheaf.observability.metrics import decrypt_failures_total
        decrypt_failures_total.labels(field=field).inc()
        raise


_blind_index_key_cache: bytes | None = None


def _blind_index_key() -> bytes:
    """Derive a dedicated HMAC key for blind indexes from the encryption key.

    Domain-separated via a fixed label so the same encryption key can produce
    unrelated subkeys for different purposes. Cached after first computation.
    """
    global _blind_index_key_cache
    if _blind_index_key_cache is None:
        raw_key = settings.get_encryption_key()
        _blind_index_key_cache = hmac.new(
            b"sheaf-blind-index-v1", raw_key, hashlib.sha256,
        ).digest()
    return _blind_index_key_cache


def blind_index(value: str) -> str:
    """Keyed HMAC-SHA-256 blind index for lookups on encrypted fields.

    Normalised (lowered, stripped) before hashing for case-insensitive lookups.
    Keyed so an attacker with a DB dump can't precompute a rainbow table over
    known email lists to reverse any `email_hash` row — they'd also need the
    in-memory encryption key.
    """
    normalised = value.strip().lower()
    return hmac.new(
        _blind_index_key(), normalised.encode(), hashlib.sha256,
    ).hexdigest()


def hash_mail_token(token: str) -> str:
    """Keyed HMAC-SHA-256 hash of a mail-delivered token for DB storage.

    Used for the password-reset and email-verification tokens. Plain
    hashlib.sha256 would let an attacker with a DB dump verify guessed
    plaintexts offline; HMAC under the JWT secret ties verification to
    the running app's in-memory key.
    """
    key = settings.jwt_secret_key.encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()
