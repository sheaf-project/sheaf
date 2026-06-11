"""Strip or rewrite hosted image references on imported entities.

A Sheaf-to-Sheaf JSON export carries `avatar_url` and bio markdown that
point at the original instance's `/v1/files/<key>` paths or bare storage
keys (e.g. `avatars/<owner_user_id>/<uuid>.png`). When the import lands
on a different account (or even the same instance under a different
user), those keys reference blobs owned by the original user.

Two modes, one code path:

- **Strip** (plain JSON import): the import has no way to fetch the
  bytes, so internal references are removed entirely and the user
  re-uploads. The `strip_*` functions are the no-map case of the
  rewrite functions below.
- **Rewrite** (export-with-images archive import): the archive importer
  re-uploads the blob bytes as the importing user's `UploadedFile` rows
  and passes an old-key -> new-key map. Mapped references are rewritten
  to the new key (and recorded in `used_keys`, so the importer can
  discard uploads nothing ended up referencing - e.g. a member the
  dedup pass skipped). Internal references with no mapping are stripped
  exactly like the plain import.

External image URLs (`https://gravatar.com/...`, `https://imgur.com/...`)
are preserved in both modes: they're not tied to anyone's storage and
they behave the same on any instance.
"""

from __future__ import annotations

import re

from sheaf.files import _MD_IMAGE_URL_RE, _to_internal_key

# Image keys live in flat strings (Member.avatar_url, System.avatar_url),
# JSON arrays (JournalEntry.image_keys, ContentRevision.image_keys), and
# markdown bodies (Member.description, JournalEntry.body, ContentRevision.body,
# Message.body, Reminder.body, Group.description). Cover all three.


def is_internal_image_ref(value: str | None) -> bool:
    """True iff `value` points at this-instance hosted storage.

    Wraps `_to_internal_key`, which recognises the three forms an
    internal reference can take: `/v1/files/<key>`, `{cdn}/<key>`,
    and a bare storage key. Returns False for None, empty, or any
    URL whose host is not ours (gravatar, imgur, etc).
    """
    if not value:
        return False
    return _to_internal_key(value) is not None


def rewrite_internal_avatar_url(
    value: str | None,
    key_map: dict[str, str],
    used_keys: set[str],
) -> str | None:
    """Rewrite (or strip) an avatar reference; gate external URLs.

    Used on `Member.avatar_url` and `System.avatar_url` at import time.
    An internal reference whose key is in `key_map` becomes the new bare
    storage key; an unmapped internal reference is nulled out. Externals
    still go through the shared importer policy gate (http(s)-only
    scheme allowlist plus the ALLOW_EXTERNAL_IMAGES instance setting) so
    an import can't smuggle in a URL the regular profile-write path
    would refuse, and a policy change between export and import is
    honoured.
    """
    if value and (key := _to_internal_key(value)) is not None:
        new_key = key_map.get(key)
        if new_key is None:
            return None
        used_keys.add(key)
        return new_key
    from sheaf.services.import_parsing import sanitize_external_avatar_url

    return sanitize_external_avatar_url(value)


def strip_internal_avatar_url(value: str | None) -> str | None:
    """Null out `value` if it's a hosted reference; gate external URLs.

    The plain-JSON-import case of `rewrite_internal_avatar_url` (no
    blobs available, so no map).
    """
    return rewrite_internal_avatar_url(value, {}, set())


def rewrite_internal_image_refs_md(
    text: str | None,
    key_map: dict[str, str],
    used_keys: set[str],
) -> str | None:
    """Rewrite `![alt](url)` embeds with mapped keys; strip unmapped internals.

    Operates on raw markdown plaintext. The regex matches the same
    embed shape `sheaf/files.py:_MD_IMAGE_URL_RE` uses (`![alt](url)`),
    and we apply `_to_internal_key` to each url to decide. Mapped
    internal embeds are re-pointed at `/v1/files/<new_key>` (the
    canonical serve path the editor writes); unmapped internal embeds
    are dropped entirely (including their alt text); externals survive
    unchanged. Surrounding markdown is untouched.

    Returns None when the input is None. An input that consisted only
    of stripped embeds may return an empty string - callers that store
    these fields as nullable should treat empty as null.
    """
    if text is None:
        return None

    def _rewrite(m: re.Match[str]) -> str:
        url = m.group(2)
        key = _to_internal_key(url)
        if key is None:
            return m.group(0)
        new_key = key_map.get(key)
        if new_key is None:
            return ""
        used_keys.add(key)
        return f"{m.group(1)}/v1/files/{new_key}{m.group(3)}"

    return _MD_IMAGE_URL_RE.sub(_rewrite, text)


def strip_internal_image_refs_md(text: str | None) -> str | None:
    """Remove `![alt](url)` embeds whose URL is internal; preserve externals.

    The plain-JSON-import case of `rewrite_internal_image_refs_md`.
    """
    return rewrite_internal_image_refs_md(text, {}, set())


def strip_internal_image_refs_md_to_none(text: str | None) -> str | None:
    """Same as `strip_internal_image_refs_md`, but collapses empty / whitespace
    results to None. For fields that are nullable in the DB and where an
    empty string is semantically the same as absent.
    """
    cleaned = strip_internal_image_refs_md(text)
    if cleaned is None:
        return None
    return cleaned if cleaned.strip() else None


def rewrite_internal_image_refs_md_to_none(
    text: str | None,
    key_map: dict[str, str],
    used_keys: set[str],
) -> str | None:
    """Rewrite variant of `strip_internal_image_refs_md_to_none`."""
    cleaned = rewrite_internal_image_refs_md(text, key_map, used_keys)
    if cleaned is None:
        return None
    return cleaned if cleaned.strip() else None


def rewrite_internal_image_keys(
    keys: list[str] | None,
    key_map: dict[str, str],
    used_keys: set[str],
) -> list[str]:
    """Remap a pre-extracted image_keys list; drop unmapped internals.

    These lists (`JournalEntry.image_keys`, `ContentRevision.image_keys`)
    are by design hosted-only - they're cached at write time precisely
    so the orphan-cleanup pass doesn't have to re-parse the markdown.
    Keys with a mapping come through as the new key (keeping the cache
    consistent with the rewritten markdown bodies); unmapped internal
    keys are dropped. Non-internal entries are preserved as-is in case
    a future change ever puts external URLs in the list (matching the
    long-standing strip behaviour).
    """
    if not keys:
        return []
    out: list[str] = []
    for k in keys:
        key = _to_internal_key(k) if k else None
        if key is None:
            out.append(k)
            continue
        new_key = key_map.get(key)
        if new_key is None:
            continue
        used_keys.add(key)
        out.append(new_key)
    return out


def strip_internal_image_keys(keys: list[str] | None) -> list[str]:
    """Filter out internal keys from a pre-extracted image_keys list.

    The plain-JSON-import case of `rewrite_internal_image_keys`: with no
    map every internal key drops, so the effective behaviour is "always
    return []" for well-formed exports.
    """
    return rewrite_internal_image_keys(keys, {}, set())
