"""OpenPlural import-residual preservation.

Sheaf's OpenPlural importer maps the subset of the format it models and,
without this module, drops the rest. That makes Sheaf a lossy hop: a
file from another app loses that app's `extensions` namespaces, its chat
and relationships modules, switch-style `front_events` / `front_comments`,
and non-tag taxonomy on the way through.

This module captures that residual on import and re-merges it into the
next OpenPlural export, so a Sheaf-in-the-middle round-trip preserves it.
This is the "baseline" tier of the preservation contract Sheaf proposed
upstream (skylartaylor/openplural#11): file-level and whole-section
passthrough. Per-record foreign `extensions` (which need stable record
identity to re-attach) are not preserved yet and are reported with a
warning; that is the follow-up "full passthrough" tier.

Storage: the residual is JSON, zlib-compressed (it can be large), then
encrypted at rest (it can carry message bodies and other content Sheaf
treats as sensitive), and parked on `System.openplural_archive`. It is
bounded by `settings.openplural_max_preserved_mb` (measured on the raw
JSON) so a hostile or huge file cannot grow the row without limit.
"""

from __future__ import annotations

import base64
import json
import zlib

from sheaf.crypto import decrypt, encrypt

# The reserved namespace Sheaf owns; everything else under file-level
# `extensions` is foreign and gets preserved.
_OWN_NS = "sheaf"

# Whole top-level objects/sections Sheaf does not translate. Captured
# verbatim. `chat` / `relationships` are spec modules Sheaf has no feature
# for; `front_comments` are time-anchored comments on fronting that Sheaf
# has no model for. NB `front_events` are NOT here: the importer converts
# them to interval fronts (see openplural_import._fronts_from_events), so
# they are consumed, not preserved.
_PASSTHROUGH_TOP_LEVEL = ("chat", "relationships", "front_comments")


def extract_residual(envelope: dict) -> dict:
    """Return the parts of an OpenPlural envelope Sheaf does not model.

    Empty dict when there is nothing to preserve. Pure: no IO.
    """
    residual: dict = {}

    # Foreign file-level extensions namespaces (everything but our own).
    ext = envelope.get("extensions")
    if isinstance(ext, dict):
        foreign = {k: v for k, v in ext.items() if k != _OWN_NS}
        if foreign:
            residual["extensions"] = foreign

    # Whole un-consumed top-level sections / modules.
    for key in _PASSTHROUGH_TOP_LEVEL:
        val = envelope.get(key)
        if val:
            residual[key] = val

    # Non-tag taxonomy: Sheaf only models tag-kind terms. Preserve the rest
    # (roles, sources, custom kinds) and their assignments.
    terms = envelope.get("taxonomy_terms")
    if isinstance(terms, list):
        non_tag = [
            t for t in terms if isinstance(t, dict) and t.get("kind") != "tag"
        ]
        if non_tag:
            residual["taxonomy_terms"] = non_tag
            non_tag_ids = {t.get("id") for t in non_tag}
            assignments = envelope.get("taxonomy_assignments")
            if isinstance(assignments, list):
                kept = [
                    a
                    for a in assignments
                    if isinstance(a, dict) and a.get("term_id") in non_tag_ids
                ]
                if kept:
                    residual["taxonomy_assignments"] = kept

    return residual


# Record arrays whose per-record `extensions` could carry a foreign
# namespace. Used only to decide whether to warn that per-record
# preservation is not done yet (the full-passthrough follow-up).
_RECORD_ARRAYS = (
    "systems",
    "members",
    "groups",
    "taxonomy_terms",
    "custom_fields",
    "front_periods",
    "notes",
)


def has_per_record_foreign_extensions(envelope: dict) -> bool:
    """True if any record carries a per-record `extensions` namespace other
    than Sheaf's own. Sheaf preserves file-level residual but not per-record
    foreign extensions yet, so this drives a one-off warning."""
    for key in _RECORD_ARRAYS:
        for rec in envelope.get(key) or []:
            if not isinstance(rec, dict):
                continue
            ext = rec.get("extensions")
            if isinstance(ext, dict) and any(ns != _OWN_NS for ns in ext):
                return True
    boards = envelope.get("boards")
    if isinstance(boards, dict):
        for post in boards.get("posts") or []:
            if isinstance(post, dict):
                ext = post.get("extensions")
                if isinstance(ext, dict) and any(ns != _OWN_NS for ns in ext):
                    return True
    return False


def merge_residual(existing: dict, incoming: dict) -> dict:
    """Combine a previously-preserved residual with a new one.

    Re-importing from a second foreign app must not wipe the first app's
    preserved data. Merge at top-level-key granularity; for `extensions`
    merge namespaces (incoming wins on a clash); for the list sections
    the incoming file replaces (it is the fresher snapshot of that
    section). Empty inputs are tolerated.
    """
    if not existing:
        return dict(incoming)
    if not incoming:
        return dict(existing)
    merged = dict(existing)
    for key, val in incoming.items():
        if key == "extensions" and isinstance(val, dict):
            base = dict(merged.get("extensions") or {})
            base.update(val)
            merged["extensions"] = base
        else:
            merged[key] = val
    return merged


def pack_residual(residual: dict, *, max_bytes: int) -> tuple[str | None, str | None]:
    """Compress + encrypt a residual for storage.

    Returns ``(token, warning)``. ``token`` is None when there is nothing
    to store or when the raw JSON exceeds ``max_bytes`` (in which case
    ``warning`` explains the drop). The size check is on the uncompressed
    JSON: it bounds what we are willing to retain, not the on-disk size.
    """
    if not residual:
        return None, None
    raw = json.dumps(residual, separators=(",", ":")).encode("utf-8")
    if max_bytes and len(raw) > max_bytes:
        return None, (
            f"preserved {len(raw)} bytes of unsupported OpenPlural data exceeds "
            f"the {max_bytes // (1024 * 1024)}MB limit "
            "(OPENPLURAL_MAX_PRESERVED_MB); it was not retained"
        )
    compressed = zlib.compress(raw, 9)
    token = encrypt(base64.b64encode(compressed).decode("ascii"))
    return token, None


def unpack_residual(token: str | None) -> dict:
    """Inverse of ``pack_residual``. Returns {} for NULL/empty/corrupt.

    Tolerant by design: a residual that fails to decode must never break
    an export. A corrupt blob yields an empty residual (the export is
    simply missing the preserved data, not failed)."""
    if not token:
        return {}
    try:
        compressed = base64.b64decode(decrypt(token))
        data = json.loads(zlib.decompress(compressed))
    except Exception:  # noqa: BLE001 - corrupt archive must not break export
        return {}
    return data if isinstance(data, dict) else {}
