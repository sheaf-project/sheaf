"""Cursor pagination helpers.

Generic encode/decode for opaque cursors keyed on (sort_value, id). The
cursor is base64url-encoded JSON; callers treat it as a black box, the
server decodes and uses it to construct a WHERE filter. The pattern is:

    SELECT ... FROM tbl
    WHERE (sort_col, id) < (cursor.sort_value, cursor.id)
    ORDER BY sort_col DESC, id DESC
    LIMIT N + 1;

The `+1` is the "is there more?" probe — fetch one extra row, return
only N, and use the leftover as the has_more signal. Avoids a separate
COUNT(*) query whose cost scales with history length.

Postgres row comparison `(a, b) < (c, d)` evaluates as
`a < c OR (a = c AND b < d)`, which is exactly the lexicographic
semantics we want for stable pagination across rows with tied sort values.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import TypedDict


class CursorPayload(TypedDict):
    """Decoded cursor: the sort_value + id pair from the last item of the
    previous page. Server uses it to filter the next query."""

    sort_value: str  # ISO-8601 timestamp string (kept as string for cross-encoding stability)
    id: str  # UUID string


def encode_cursor(sort_value: datetime, item_id: uuid.UUID) -> str:
    """Encode a (sort_value, id) pair as an opaque base64url JSON cursor."""
    payload = {"s": sort_value.isoformat(), "i": str(item_id)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Decode a cursor back into (sort_value, id). Raises ValueError on
    any malformed input — callers should translate to a 400 response."""
    try:
        # Re-pad — urlsafe_b64encode without padding loses the trailing =s,
        # which b64decode is picky about.
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(raw)
        sort_value = datetime.fromisoformat(payload["s"])
        item_id = uuid.UUID(payload["i"])
        return sort_value, item_id
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError("Invalid cursor") from exc
