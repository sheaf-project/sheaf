"""Extended-tier metrics: opt-in, `sheaf_ext_*`, and gated in exactly one place.

The default tier (everything else under sheaf/observability/) is bounded,
aggregate, and holds nothing per account. This tier is where the metrics
with multiplied label sets or short-lived per-account state live: useful to
an operator who wants to know which client version is still out there, or
which route one platform hammers and another does not, and a scrape cost
and a data-handling posture that every self-hoster should not inherit by
default. So it is off unless `METRICS_EXTENDED=true`.

Two rules make the gate trustworthy:

- **One gate, here.** Nothing in this module is registered, written, or
  read unless `ENABLED` is true, and callers go through the functions below
  rather than touching the metric objects. When the flag is off the objects
  are never created, so the series do not exist: there is no zero to alert
  on and nothing can half-leak because someone forgot an `if` at a call
  site.
- **A name prefix, not a label.** Every metric here is `sheaf_ext_*` and
  every Redis key is `sheaf:ext:*`, so one regex finds the lot in the repo
  (`grep -r sheaf_ext_`), in a pipeline (`{__name__=~"sheaf_ext_.*"}` in a
  relabel or recording rule, to route it to a short-retention index), and
  in Redis (`SCAN sheaf:ext:*`). A `tier="extended"` label would work in a
  pipeline but is invisible in code and easy to drop when copying a
  definition.

Per-account state in this tier is folded under the DAY-SALTED token
(`usage._day_salted_token`), never the stable one the DAU/MAU sketches use.
A stable token is fine inside a HyperLogLog, whose members are never read
back; anything that could be read back must not be joinable from one day to
the next, and the salt is what stops a token seen on day N being matched to
day N+1. All `sheaf:ext:` keys carry a 48-hour TTL (long enough for a
worker that was briefly down to still see yesterday at the day boundary,
short enough that nothing accumulates), and `sweep_extended_keys` deletes
anything older than yesterday outright as a backstop to the TTL.

First metric, so the scaffolding ships with a use:
`sheaf_ext_active_accounts_by_version{client_family, version}`, distinct
accounts active today per client family and `major.minor` version, read
from a per-(family, version) day sketch. It answers "how long do we keep
the API compatibility shim for 1.2" and it is extended-tier because every
release adds series on every instance whether or not the operator cares.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sheaf.config import settings
from sheaf.observability.client_family import (
    INTERACTIVE_FAMILIES,
    client_version_from,
)

logger = logging.getLogger("sheaf.metrics.extended")

# The one gate. Evaluated once at import, like the rest of the metrics
# wiring (the registry itself is decided at import), so a process either has
# this tier or it does not.
ENABLED: bool = bool(settings.metrics_enabled and settings.metrics_extended)

METRIC_PREFIX = "sheaf_ext_"
KEY_PREFIX = "sheaf:ext:"
# 48 hours: today's keys are the only ones ever read, and the extra day
# covers a gauge refresh that runs just after midnight reading "today" as
# yesterday, or a worker that was down across the boundary.
EXT_KEY_TTL_SECONDS = 48 * 3600

# Where new (family, version) pairs land once a day has seen
# `metrics_extended_version_pairs_per_day` of them. The version label comes
# from a client-controlled header, and this fold is what stops a client
# sending a fresh version string per request from minting an unbounded
# number of keys and series: the worst case is the cap plus this.
VERSION_OTHER = "other"

# Metric objects exist only when the tier is on. `_G` is the same
# constructor the default tier uses, so the multiprocess binding rules are
# identical; the difference is purely that this branch is not taken when
# the flag is off.
if ENABLED:
    from sheaf.observability.metrics import _G

    active_accounts_by_version = _G(
        "sheaf_ext_active_accounts_by_version",
        "Distinct accounts active today per client family and major.minor "
        "client version. Extended tier (METRICS_EXTENDED). A version no longer "
        "seen today reads 0 until the process restarts, then disappears.",
        ["client_family", "version"],
    )
else:  # pragma: no cover - the off branch is exercised by the gate test
    active_accounts_by_version = None


def _day_str(day: date) -> str:
    return day.isoformat()


def version_day_key(family: str, version: str, day: date) -> str:
    """Day sketch of accounts seen on one (family, version), e.g.
    sheaf:ext:hll:ver:android:1.2:2026-09-22."""
    return f"{KEY_PREFIX}hll:ver:{family}:{version}:{_day_str(day)}"


def versions_seen_key(day: date) -> str:
    """The bounded set of `family:version` pairs seen today, which is what
    the read side iterates (rather than SCANning for sketch keys)."""
    return f"{KEY_PREFIX}versions:{_day_str(day)}"


async def record_client_version(
    r,
    pipe,
    family: str,
    client_header: str | None,
    day: date,
    token: str,
) -> None:
    """Queue, onto the caller's pipeline, the writes that mark `token` active
    today on this family and client version.

    Called from `usage._record_active` inside its fire-and-forget task, with
    the DAY-SALTED token already computed by the caller: this function never
    sees an id. It does two reads of its own (is this pair already known
    today, and how many pairs are known) to enforce the per-day pair cap
    (`metrics_extended_version_pairs_per_day`), then appends to the pipeline
    the caller executes. No-op when the tier is off or the family is not an
    interactive one.
    """
    if not ENABLED or family not in INTERACTIVE_FAMILIES:
        return
    version = client_version_from(client_header)
    seen_key = versions_seen_key(day)
    member = f"{family}:{version}"
    if (
        not await r.sismember(seen_key, member)
        and int(await r.scard(seen_key))
        >= settings.metrics_extended_version_pairs_per_day
    ):
        version = VERSION_OTHER
        member = f"{family}:{version}"
    pipe.sadd(seen_key, member)
    pipe.expire(seen_key, EXT_KEY_TTL_SECONDS)
    key = version_day_key(family, version, day)
    pipe.pfadd(key, token)
    pipe.expire(key, EXT_KEY_TTL_SECONDS)


# (family, version) pairs this process has ever published, so a pair that
# drops out of today's set is set to 0 rather than left at yesterday's value.
# Bounded by the per-day pair cap times the days the process lives.
_published: set[tuple[str, str]] = set()


async def refresh_extended_gauges() -> None:
    """Set every extended-tier gauge from today's `sheaf:ext:` keys.

    Called from the slow gauge pass after the default-tier usage gauges.
    Best-effort like them: a Redis error leaves the gauges at their last
    value rather than raising into the pass or zeroing them.
    """
    if not ENABLED:
        return
    try:
        from sheaf.auth.sessions import get_redis_bytes

        rb = await get_redis_bytes()
        today = datetime.now(UTC).date()
        live: set[tuple[str, str]] = set()
        for raw in await rb.smembers(versions_seen_key(today)):
            member = raw.decode() if isinstance(raw, bytes) else str(raw)
            family, _, version = member.partition(":")
            # The set is ours, but the guard costs nothing and keeps the
            # label space closed even if the key is ever tampered with.
            if family not in INTERACTIVE_FAMILIES or not version:
                continue
            count = int(await rb.pfcount(version_day_key(family, version, today)))
            active_accounts_by_version.labels(
                client_family=family, version=version
            ).set(count)
            live.add((family, version))
        for family, version in _published - live:
            active_accounts_by_version.labels(
                client_family=family, version=version
            ).set(0)
        _published.update(live)
    except Exception:
        logger.debug("extended: gauge refresh failed", exc_info=True)


async def sweep_extended_keys() -> dict:
    """Delete `sheaf:ext:*` keys whose day is older than yesterday.

    Every key already carries a 48-hour TTL; this is the belt to that
    braces, so the retention promise ("nothing per account outlives two
    days") does not rest on a TTL alone. The day is the last colon-separated
    segment of every key in this namespace by construction.
    """
    if not ENABLED:
        return {"items_processed": 0}
    from sheaf.auth.sessions import get_redis

    r = await get_redis()
    today = datetime.now(UTC).date()
    keep = {_day_str(today), _day_str(today - timedelta(days=1))}
    deleted = 0
    async for key in r.scan_iter(match=f"{KEY_PREFIX}*", count=200):
        name = key.decode() if isinstance(key, bytes) else str(key)
        if name.rsplit(":", 1)[-1] not in keep:
            await r.delete(name)
            deleted += 1
    return {"items_processed": deleted}
