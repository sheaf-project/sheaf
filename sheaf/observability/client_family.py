"""Which kind of client made a request, as a bounded label.

The raw `X-Sheaf-Client` header is free text: the official apps send
`Sheaf Android/1.2.0`, but a third-party client can and does send anything
(`My Custom App/0.5`). A free-text value can never be a metric label - the
cardinality rules in docs/METRICS.md exist precisely so a stranger cannot
mint series on our Prometheus by changing a header. So the header is folded
into one of a fixed set of families here, and only the family ever reaches a
label or a sketch key.

The credential decides `api`, not the header. An API key is automation
whatever its user agent claims, and a session or JWT is a person at a client
whatever it claims; the header describes the client, the credential describes
the relationship. `api` is therefore checked first and is not overridable.

There is deliberately no User-Agent fallback for `web`. The browser parse the
sessions UI uses would make this easy, but "a browser talked to the API" is
not "the Sheaf web app": a third-party web client, or a curl with a browser
UA, would be counted as ours. The header is the claim; the honest fallback is
`other`. If `other` is ever large, that is a finding, not a bucket to hide.
"""

from __future__ import annotations

import re
from typing import Literal, get_args

ClientFamily = Literal["web", "android", "ios", "watch", "api", "other"]
CLIENT_FAMILIES: tuple[str, ...] = get_args(ClientFamily)

FAMILY_WEB = "web"
FAMILY_ANDROID = "android"
FAMILY_IOS = "ios"
FAMILY_WATCH = "watch"
FAMILY_API = "api"
FAMILY_OTHER = "other"

# The families a session or JWT can resolve to, i.e. everything that is not
# automation. The `client` auth kind is the union of exactly these.
INTERACTIVE_FAMILIES: tuple[str, ...] = (
    FAMILY_WEB, FAMILY_ANDROID, FAMILY_IOS, FAMILY_WATCH, FAMILY_OTHER,
)

# Header prefix -> family. Matched case-insensitively against the start of the
# header. Order matters only where one prefix is a prefix of another, which
# none of these are; keep it that way or move to longest-match.
#
# The watch prefixes are what the apps are expected to send once their watch
# targets identify themselves (the iOS watch shares the phone's client today
# and so lands as `ios` until then - see the design doc). They are here now so
# the day the watch build ships, nothing server-side needs to change.
_PREFIXES: tuple[tuple[str, str], ...] = (
    ("sheaf web/", FAMILY_WEB),
    ("sheaf android/", FAMILY_ANDROID),
    ("sheaf ios/", FAMILY_IOS),
    ("sheaf watchos/", FAMILY_WATCH),
    ("sheaf wear/", FAMILY_WATCH),
)


def client_family_from(
    client_header: str | None, *, is_api_key: bool
) -> str:
    """Fold a request's `X-Sheaf-Client` header and credential kind into a family.

    Pure and total: never raises, never returns anything outside
    `CLIENT_FAMILIES`, so callers can use the result as a label unguarded.
    """
    if is_api_key:
        return FAMILY_API
    if not client_header:
        return FAMILY_OTHER
    lowered = client_header.strip().lower()
    for prefix, family in _PREFIXES:
        if lowered.startswith(prefix):
            return family
    return FAMILY_OTHER


def client_family_from_name(client_name: str | None) -> str:
    """Family for a STORED session `client_name`, for the sessions gauge.

    A stored name is the header verbatim for the apps, or the browser name
    from the User-Agent parse for a web session minted before the web app
    sent a header (or by a third-party browser client). A browser name does
    not start with any official prefix and so lands as `other`, which is the
    correct reading: the session cannot prove it belongs to the Sheaf web
    app. Sessions minted by an API key do not exist, so `api` is unreachable
    here by construction.
    """
    return client_family_from(client_name, is_api_key=False)


# --- Client version ---------------------------------------------------------
#
# Extended tier only (sheaf/observability/extended.py). The version is the
# part of the header after the family prefix, bucketed to `major.minor`:
# patch releases would multiply the series for no decision anyone makes at
# patch granularity. Anything that does not parse is `unknown`, never the raw
# string, and the digit caps keep a hostile header from producing a label
# that is anything other than two short integers.

VERSION_UNKNOWN = "unknown"
_VERSION_RE = re.compile(r"^\s*(\d{1,4})\.(\d{1,4})(?:[^\d]|$)")


def client_version_from(client_header: str | None) -> str:
    """`Sheaf Android/1.2.0` -> `1.2`. Pure and total, like the family parser.

    Only a header with a recognised family prefix yields a version: a
    third-party client's version is not something this taxonomy tracks, and
    it lands as `unknown` alongside its `other` family.
    """
    if not client_header:
        return VERSION_UNKNOWN
    stripped = client_header.strip()
    lowered = stripped.lower()
    for prefix, _family in _PREFIXES:
        if lowered.startswith(prefix):
            match = _VERSION_RE.match(stripped[len(prefix):])
            if match is None:
                return VERSION_UNKNOWN
            return f"{int(match.group(1))}.{int(match.group(2))}"
    return VERSION_UNKNOWN
