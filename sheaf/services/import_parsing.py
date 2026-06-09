"""Shared parsing utilities for import handlers.

Defensive primitives every importer (PK / TB / SP / Sheaf) leans on:
JSON parsing with element-count caps, schema-shape sanity checks,
helpers for normalising string fields.

JSON parsing notes: CPython's `json.loads` is iterative under the hood,
so deeply-nested JSON doesn't blow the call stack. The DoS vector we
actually care about is the size of the resulting Python object graph
— a 1KB input that decodes to millions of nested objects. We cap the
*element count* post-parse with a cheap stack walk; the upstream
100MB request-body cap covers the input byte count.

The defaults here are sized for real-world import payloads with a
generous margin: PK exports with ~10k switches plus a few hundred
members + groups still come in well under 100k elements.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

# Per-payload element cap. PK / TB / SP / Sheaf exports of plausible
# real systems are in the low tens of thousands of elements at most;
# 5M leaves headroom for an order-of-magnitude growth without making
# the walk slow.
DEFAULT_MAX_ELEMENTS = 5_000_000


class ImportPayloadError(ValueError):
    """Raised when an import payload fails defensive parsing — bad JSON,
    too many elements, wrong top-level shape.

    The runner catches this and maps it to a `level='error', stage='parse'`
    event plus a failed job status. The message is user-facing, so keep
    it short and specific (no stack traces, no library names)."""


def sanitize_external_avatar_url(url: Any) -> str | None:
    """Policy gate for avatar URLs carried in third-party exports.

    Importers can only ever produce *external* references (the source
    app's CDN), so anything kept must be plain http(s) - a crafted
    export carrying a javascript:/data: URL must not land in a profile
    field - and externals are dropped entirely when the instance
    forbids hotlinking (ALLOW_EXTERNAL_IMAGES=false), the same policy
    the regular profile-write path enforces via normalize_avatar_url.

    Every importer routes source avatar URLs through here so the rules
    can't drift between formats.
    """
    from sheaf.config import settings

    if not url or not isinstance(url, str):
        return None
    if not url.startswith(("http://", "https://")):
        return None
    if not settings.allow_external_images:
        return None
    return url[:500]


def safe_json_loads(
    data: bytes | str,
    *,
    max_elements: int = DEFAULT_MAX_ELEMENTS,
) -> Any:
    """Parse JSON and cap the element count of the resulting object graph.

    Raises `ImportPayloadError` on parse failure or cap overflow. The
    caller is responsible for shape validation (e.g. "this should be a
    dict with key X"); this helper only guards against the parse-time
    DoS surface.
    """
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportPayloadError(f"invalid JSON: {exc}") from exc

    count = 0
    stack: list[Any] = [parsed]
    while stack:
        node = stack.pop()
        count += 1
        if count > max_elements:
            raise ImportPayloadError(
                f"payload exceeds {max_elements} elements (DoS guard)"
            )
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return parsed


def parse_options[OptionsT: BaseModel](
    payload_metadata: dict | None, model_cls: type[OptionsT]
) -> OptionsT:
    """Pull the `options` dict out of an ImportJob's payload_metadata and
    Pydantic-validate it against the source's options model.

    Missing / null / empty options means 'all defaults'. Invalid options
    is a hard `ImportPayloadError` — the frontend shouldn't be able to
    produce them, so a raise here means a client bug or hand-rolled
    request, and failing the job loudly beats importing with a silently
    wrong option set.
    """
    raw = (payload_metadata or {}).get("options")
    if raw is None or raw == {}:
        return model_cls()
    if not isinstance(raw, dict):
        raise ImportPayloadError(
            f"options must be a JSON object (got {type(raw).__name__})"
        )
    try:
        return model_cls.model_validate(raw)
    except ValidationError as exc:
        raise ImportPayloadError(f"invalid import options: {exc.errors()}") from exc


def expect_dict(parsed: Any, *, descriptor: str) -> dict:
    """Assert the parsed top-level is a JSON object (dict). PK exports,
    TB exports, SP exports, and Sheaf re-imports all share this shape;
    refusing arrays / scalars early is cleaner than letting `.get()`
    fail mid-import."""
    if not isinstance(parsed, dict):
        raise ImportPayloadError(
            f"{descriptor} must be a JSON object (got {type(parsed).__name__})"
        )
    return parsed
