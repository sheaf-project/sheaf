"""Custom-field-value encryption helpers.

CustomFieldValue.value holds arbitrary user-supplied content (text answers,
selected options, dates, etc.). It's encrypted at rest: the JSON-serialised
plaintext is encrypted, and the resulting ciphertext string is stored in the
JSONB column. JSONB happily accepts a JSON string as a top-level value.
"""

from __future__ import annotations

import json
from typing import Any

from sheaf.config import settings
from sheaf.crypto import decrypt, encrypt
from sheaf.encrypted_fields import custom_field_value_aad
from sheaf.models.custom_field import CustomFieldValue


def encrypt_field_value(value: Any, value_id) -> str:
    """JSON-serialise then encrypt a custom-field value."""
    return encrypt(json.dumps(value), aad=custom_field_value_aad(value_id))


def decrypt_field_value(stored: Any, value_id) -> Any:
    """Decrypt + JSON-decode a stored custom-field value.

    Returns None if the column was NULL. Stored ciphertext is always a string;
    a non-string is a legacy plaintext row from before encryption, passed
    through untouched only while the v1 dual-read window is open. Once the
    operator sets FIELD_ENCRYPTION_ACCEPT_V1=false they have declared no
    legacy rows remain, so the passthrough is deliberately closed: returning
    the raw stored value would be an unauthenticated injection channel, so it
    becomes None (rendered the same as a NULL column by every caller).
    """
    if stored is None:
        return None
    if not isinstance(stored, str):
        if settings.field_encryption_accept_v1:
            return stored
        return None
    return json.loads(decrypt(stored, aad=custom_field_value_aad(value_id)))


def field_value_plaintext(v: CustomFieldValue) -> Any:
    """Return the decrypted, JSON-decoded plaintext for a value row."""
    return decrypt_field_value(v.value, v.id)
