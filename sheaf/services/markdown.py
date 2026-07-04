"""Shared markdown utilities.

Single source of truth for "what counts as an image reference" so file
cleanup, journal write-time key extraction, and bio revision capture all
agree.

The actual matching - the markdown image regex plus internal-key resolution -
lives in ``sheaf.files``. This module reuses it rather than keeping its own,
narrower matcher: that divergence is what caused the 2026-07-03 orphan-cleanup
over-deletion. A legacy reference stored as a CDN-hostname URL (e.g.
``https://images.sheaf.sh/bios/...``) resolved fine on the serve path (which
uses ``_to_internal_key``) but slipped past this module's old
``![...](/v1/files/...)``-only regex, so the still-referenced blob looked
orphaned and got reaped.
"""

from sheaf.files import _MD_IMAGE_URL_RE, _to_internal_key


def extract_image_keys(text: str | None) -> list[str]:
    """Return storage keys referenced by hosted image embeds in markdown text.

    Recognises all three stored forms of an internal reference - the app serve
    path (``/v1/files/<key>``), the legacy CDN-hostname URL
    (``{s3_public_url}/<key>``), and a bare storage key - by routing each
    embed's URL through the canonical resolver. External URLs (Gravatar,
    dicebear, a user-typed link) resolve to ``None`` and are dropped. Returns a
    sorted, deduplicated list so rows that persist this in JSON are stable
    across rewrites.
    """
    if not text:
        return []
    keys: set[str] = set()
    for match in _MD_IMAGE_URL_RE.finditer(text):
        key = _to_internal_key(match.group(2))
        if key is not None:
            keys.add(key)
    return sorted(keys)
