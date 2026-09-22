"""Which link-unfurling service fetched a preview card, as a bounded label.

A User-Agent is free text from a stranger, so it can never be a label; the
same rule that folds `X-Sheaf-Client` into a client family folds the crawler's
UA into one of a fixed set of services here. The set is the handful of places
people actually paste links, plus `other` for everything else, including the
long tail of the proxy's own allowlist (LinkedIn, Skype, Reddit, iframely,
embedly, ...) that is real but not worth a series each.

What this answers is "where do people paste their profile links", which is a
product question the supply-side metrics cannot see. It says nothing about who
pasted or who clicked: an unfurl is a service fetching a document, once per
paste, on behalf of a channel.
"""

from __future__ import annotations

from typing import Literal, get_args

Unfurler = Literal[
    "discord",
    "slack",
    "telegram",
    "mastodon",
    "matrix",
    "bluesky",
    "twitter",
    "facebook",
    "whatsapp",
    "other",
]
UNFURLERS: tuple[str, ...] = get_args(Unfurler)

# Substring markers, matched case-insensitively anywhere in the UA. Kept in
# step with the allowlist the shipped proxy configs route on
# (docker/aio/Caddyfile), which is the superset: anything that config lets
# through and this table does not name is `other`.
_MARKERS: tuple[tuple[str, str], ...] = (
    ("discordbot", "discord"),
    ("slackbot", "slack"),
    ("telegrambot", "telegram"),
    ("mastodon", "mastodon"),
    ("synapse", "matrix"),
    ("bluesky", "bluesky"),
    ("cardyb", "bluesky"),
    ("twitterbot", "twitter"),
    ("facebookexternalhit", "facebook"),
    ("facebot", "facebook"),
    ("whatsapp", "whatsapp"),
)


def unfurler_from(user_agent: str | None) -> str:
    """Fold a crawler's User-Agent into an `Unfurler`. Pure and total."""
    if not user_agent:
        return "other"
    lowered = user_agent.lower()
    for marker, name in _MARKERS:
        if marker in lowered:
            return name
    return "other"
