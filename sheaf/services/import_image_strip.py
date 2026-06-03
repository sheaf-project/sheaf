"""Strip hosted image references from imported entities.

A Sheaf-to-Sheaf JSON export carries `avatar_url` and bio markdown that
point at the original instance's `/v1/files/<key>` paths or bare storage
keys (e.g. `avatars/<owner_user_id>/<uuid>.png`). When the import lands
on a different account (or even the same instance under a different
user), those keys reference blobs owned by the original user. The
import currently has no way to fetch and re-host the bytes, so we
strip the references entirely and let the user re-upload.

External image URLs (`https://gravatar.com/...`, `https://imgur.com/...`)
are preserved as-is: they're not tied to anyone's storage and they
behave the same on any instance.

The proper long-term fix is the export-with-images zip path: the export
ships the blob bytes alongside the JSON, the importer re-uploads them
as the importer's `UploadedFile` rows, and the references get rewritten
to the new keys. Tracked in project_future_work.
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


def strip_internal_avatar_url(value: str | None) -> str | None:
    """Null out `value` if it's a hosted reference; pass external URLs through.

    Used on `Member.avatar_url` and `System.avatar_url` at import time.
    """
    return None if is_internal_image_ref(value) else value


def strip_internal_image_refs_md(text: str | None) -> str | None:
    """Remove `![alt](url)` embeds whose URL is internal; preserve externals.

    Operates on raw markdown plaintext. The regex matches the same
    embed shape `sheaf/files.py:_MD_IMAGE_URL_RE` uses (`![alt](url)`),
    and we apply `_to_internal_key` to each url to decide. Internal
    matches are dropped entirely (including their alt text); externals
    survive unchanged. Surrounding markdown (paragraphs, links, headings)
    is untouched.

    Returns None when the input is None. An input that consisted only
    of internal embeds may return an empty string — callers that store
    these fields as nullable should treat empty as null.
    """
    if text is None:
        return None

    def _maybe_strip(m: re.Match[str]) -> str:
        url = m.group(2)
        return "" if _to_internal_key(url) is not None else m.group(0)

    return _MD_IMAGE_URL_RE.sub(_maybe_strip, text)


def strip_internal_image_refs_md_to_none(text: str | None) -> str | None:
    """Same as `strip_internal_image_refs_md`, but collapses empty / whitespace
    results to None. For fields that are nullable in the DB and where an
    empty string is semantically the same as absent.
    """
    cleaned = strip_internal_image_refs_md(text)
    if cleaned is None:
        return None
    return cleaned if cleaned.strip() else None


def strip_internal_image_keys(keys: list[str] | None) -> list[str]:
    """Filter out internal keys from a pre-extracted image_keys list.

    These lists (`JournalEntry.image_keys`, `ContentRevision.image_keys`)
    are by design hosted-only — they're cached at write time precisely
    so the orphan-cleanup pass doesn't have to re-parse the markdown.
    A foreign instance's keys in here are always wrong on import, so
    the effective behaviour is "always return []" — we still iterate
    in case a future change ever puts external URLs in the list.
    """
    if not keys:
        return []
    return [k for k in keys if not is_internal_image_ref(k)]
