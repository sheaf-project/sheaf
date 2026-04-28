"""Shared markdown utilities.

Single source of truth for "what counts as an image reference" so file
cleanup, journal write-time key extraction, and bio revision capture
all agree.
"""

import re

# Matches markdown image references: ![...](/v1/files/...)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((/v1/files/[^)]+)\)")

# All hosted file URLs start with this prefix
_FILE_PREFIX = "/v1/files/"


def extract_image_keys(text: str | None) -> list[str]:
    """Return storage keys referenced by hosted image embeds in markdown text.

    Strips the /v1/files/ prefix and any signed-URL query params, leaving the
    bare storage key (e.g. "uploads/<user>/<uuid>.png"). Returns a sorted,
    deduplicated list so rows that store this in JSON are stable across rewrites.
    """
    if not text:
        return []
    keys: set[str] = set()
    for match in _MD_IMAGE_RE.finditer(text):
        url = match.group(1)
        if url.startswith(_FILE_PREFIX):
            key = url[len(_FILE_PREFIX):]
            if "?" in key:
                key = key.split("?", 1)[0]
            keys.add(key)
    return sorted(keys)
