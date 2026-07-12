"""IANA timezone validation for the global display-timezone preference.

`System.timezone` stores an IANA zone name (e.g. "America/New_York") or NULL,
where NULL means "auto" - each device renders in its own local clock. Any
non-null value written through the API or restored from an import must be a
real zone, so both the schema validator and the importer route through
`is_valid_timezone` here.

We depend on the `tzdata` package rather than the system zone database so the
valid-zone set is identical everywhere: the backend container base
(python:3.12-slim) does not reliably ship /usr/share/zoneinfo, which would make
`available_timezones()` return a near-empty set and reject every zone in prod
while passing locally. The set includes the city zones plus the generic
fixed-offset zones the picker offers ("EST", "EST5EDT", "Etc/GMT+5", ...).
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones


@lru_cache(maxsize=1)
def _valid_timezones() -> frozenset[str]:
    # available_timezones() rebuilds its set on every call; cache it once.
    return frozenset(available_timezones())


def is_valid_timezone(value: str) -> bool:
    """True if `value` is a known IANA zone name. Does not accept the "auto"
    sentinel - callers represent auto as NULL/None, not a string."""
    return value in _valid_timezones()


def localize(dt: datetime, tz: str | None) -> datetime:
    """Convert an aware datetime into `tz` (an IANA zone), or UTC when `tz` is
    None / unset / unknown.

    Server-side counterpart to the web formatter, for the handful of places the
    backend renders a timestamp for a human (emails, notification bodies). The
    account "automatic" case (None) collapses to UTC so the emitted zone stamp
    (`strftime("%Z")`) is honest about the zone the reader is seeing, rather
    than guessing a device zone the server can't know."""
    if tz and is_valid_timezone(tz):
        return dt.astimezone(ZoneInfo(tz))
    return dt.astimezone(UTC)
