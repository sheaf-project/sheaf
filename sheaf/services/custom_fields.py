"""Custom-field-value encryption helpers.

CustomFieldValue.value holds arbitrary user-supplied content (text answers,
selected options, dates, etc.). It's encrypted at rest: the JSON-serialised
plaintext is encrypted, and the resulting ciphertext string is stored in the
JSONB column. JSONB happily accepts a JSON string as a top-level value.
"""

from __future__ import annotations

import json
from typing import Any

from sheaf.crypto import decrypt, encrypt
from sheaf.models.custom_field import CustomFieldValue


def encrypt_field_value(value: Any) -> str:
    """JSON-serialise then encrypt a custom-field value."""
    return encrypt(json.dumps(value))


def decrypt_field_value(stored: Any) -> Any:
    """Decrypt + JSON-decode a stored custom-field value.

    Returns None if the column was NULL. Stored ciphertext is always a string;
    if we encounter a non-string (e.g. legacy plaintext rows from before
    encryption), pass it through untouched as a defensive fallback.
    """
    if stored is None:
        return None
    if not isinstance(stored, str):
        return stored
    return json.loads(decrypt(stored))


def field_value_plaintext(v: CustomFieldValue) -> Any:
    """Return the decrypted, JSON-decoded plaintext for a value row."""
    return decrypt_field_value(v.value)
