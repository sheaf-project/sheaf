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
- **A name prefix, not a label.** Every metric here is `sheaf_ext_*`,
  every Redis key is `sheaf:ext:*`, and every setting is
  `METRICS_EXTENDED_*`, so one regex finds the lot in the repo
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
short enough that nothing accumulates), and the hourly job folds yesterday's
per-account counters into histograms, deletes them, and sweeps anything
older outright as a backstop to the TTL.

What the tier holds per account, and for how long:

- `sheaf:ext:hll:ver:{family}:{version}:{day}`: a sketch (never read back)
  of accounts on each client version. Distinct-count only.
- `sheaf:ext:hll:famage:{family}:{age}:{day}`: the same, split by account
  age bucket. Distinct-count only.
- `sheaf:ext:famset:{family}:{day}`: the SET of day-salted tokens seen on
  each family. Read back only by Redis set algebra on the server
  (intersections and differences between families), never transferred to
  the app: the pattern gauge is a cardinality per combination.
- `sheaf:ext:reqs:{family}:{day}`: HASH token -> request count. The one
  thing that is read back, once, the day after, to be observed into a
  histogram and deleted. It is a count per pseudonymous token for one day
  and nothing else.
- `sheaf:ext:pubreqs:{subject_type}:{day}`: HASH of day-salted SYSTEM token
  -> served public-profile requests, folded the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sheaf.config import settings
from sheaf.observability.client_family import (
    CLIENT_FAMILIES,
    INTERACTIVE_FAMILIES,
    client_family_from,
    client_version_from,
)

logger = logging.getLogger("sheaf.metrics.extended")

# The one gate. Evaluated once at import, like the rest of the metrics
# wiring (the registry itself is decided at import), so a process either has
# this tier or it does not.
ENABLED: bool = bool(settings.metrics_enabled and settings.metrics_extended)

METRIC_PREFIX = "sheaf_ext_"
KEY_PREFIX = "sheaf:ext:"
# 48 hours: today's keys are the only ones ever read live, yesterday's are
# read once by the fold, and the extra day covers a refresh or a fold that
# runs just after midnight, or a worker that was down across the boundary.
EXT_KEY_TTL_SECONDS = 48 * 3600

# Where new (family, version) pairs land once a day has seen
# `metrics_extended_version_pairs_per_day` of them. The version label comes
# from a client-controlled header, and this fold is what stops a client
# sending a fresh version string per request from minting an unbounded
# number of keys and series: the worst case is the cap plus this.
VERSION_OTHER = "other"

# Public-profile subject types, mirrored from the default-tier counter so the
# two cannot disagree about the vocabulary.
PUBLIC_SUBJECT_TYPES: tuple[str, ...] = ("public", "link")

# Metric objects exist only when the tier is on. `_C` / `_G` / `_H` are the
# same constructors the default tier uses, so the multiprocess binding rules
# are identical; the difference is purely that this branch is not taken when
# the flag is off.
if ENABLED:
    from sheaf.observability.buckets import DAILY_REQUEST_BUCKETS
    from sheaf.observability.metrics import _C, _G, _H

    active_accounts_by_version = _G(
        "sheaf_ext_active_accounts_by_version",
        "Distinct accounts active today per client family and major.minor "
        "client version. Extended tier (METRICS_EXTENDED). A version no longer "
        "seen today reads 0 until the process restarts, then disappears.",
        ["client_family", "version"],
    )
    http_requests_by_client_total = _C(
        "sheaf_ext_http_requests_by_client_total",
        "HTTP requests by route template, method, status class and client "
        "family: the default-tier RED counter multiplied by family, which is "
        "why it is extended tier. Extended tier (METRICS_EXTENDED).",
        ["method", "route", "status_class", "client_family"],
    )
    accounts_by_client_pattern = _G(
        "sheaf_ext_accounts_by_client_pattern",
        "Distinct accounts active today by the EXACT combination of client "
        "families they used (web, web+android, api+web, ...). Computed by "
        "set algebra inside Redis; no account is ever read out. Extended tier "
        "(METRICS_EXTENDED).",
        ["pattern"],
    )
    account_requests_daily = _H(
        "sheaf_ext_account_requests_daily",
        "Distribution of authenticated requests per account per day, by "
        "client family, observed once per account per day by the fold job "
        "from yesterday's counters. Extended tier (METRICS_EXTENDED).",
        ["client_family"],
        buckets=DAILY_REQUEST_BUCKETS,
    )
    active_accounts_daily_by_family_age = _G(
        "sheaf_ext_active_accounts_daily",
        "Distinct accounts active today per client family and account-age "
        "bucket: whether new signups start on mobile. Extended tier "
        "(METRICS_EXTENDED).",
        ["client_family", "account_age"],
    )
    public_requests_per_profile_daily = _H(
        "sheaf_ext_public_requests_per_profile_daily",
        "Distribution of served public-profile requests per profile per day, "
        "by grant subject type, observed once per profile per day by the fold "
        "job from yesterday's counters. Extended tier (METRICS_EXTENDED).",
        ["subject_type"],
        buckets=DAILY_REQUEST_BUCKETS,
    )
else:  # pragma: no cover - the off branch is exercised by the gate test
    active_accounts_by_version = None
    http_requests_by_client_total = None
    accounts_by_client_pattern = None
    account_requests_daily = None
    active_accounts_daily_by_family_age = None
    public_requests_per_profile_daily = None


def _day_str(day: date) -> str:
    return day.isoformat()


def _today() -> date:
    return datetime.now(UTC).date()


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def version_day_key(family: str, version: str, day: date) -> str:
    """Day sketch of accounts seen on one (family, version), e.g.
    sheaf:ext:hll:ver:android:1.2:2026-09-22."""
    return f"{KEY_PREFIX}hll:ver:{family}:{version}:{_day_str(day)}"


def versions_seen_key(day: date) -> str:
    """The bounded set of `family:version` pairs seen today, which is what
    the read side iterates (rather than SCANning for sketch keys)."""
    return f"{KEY_PREFIX}versions:{_day_str(day)}"


def family_age_key(family: str, age: str, day: date) -> str:
    """Day sketch of accounts seen on one family within one age bucket."""
    return f"{KEY_PREFIX}hll:famage:{family}:{age}:{_day_str(day)}"


def family_set_key(family: str, day: date) -> str:
    """The SET of day-salted tokens seen on one family today."""
    return f"{KEY_PREFIX}famset:{family}:{_day_str(day)}"


def requests_key(family: str, day: date) -> str:
    """HASH token -> authenticated requests today on one family."""
    return f"{KEY_PREFIX}reqs:{family}:{_day_str(day)}"


def public_requests_key(subject_type: str, day: date) -> str:
    """HASH day-salted system token -> served public requests today."""
    return f"{KEY_PREFIX}pubreqs:{subject_type}:{_day_str(day)}"


def _scratch_key(tag: str) -> str:
    return f"{KEY_PREFIX}scratch:{tag}:{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Write side
# ---------------------------------------------------------------------------


async def record_activity(
    r,
    pipe,
    family: str,
    client_header: str | None,
    account_age: str | None,
    day: date,
    token: str,
) -> None:
    """Queue, onto the caller's pipeline, everything the tier records for one
    authenticated request.

    Called from `usage._record_active` inside its fire-and-forget task, with
    the DAY-SALTED token already computed by the caller: this function never
    sees an id. The raw header comes in; only a bounded family and version
    go anywhere near a key. No-op when the tier is off or the family is not
    one of ours.

    Per request: the family's token set (for the pattern gauge), the
    per-token request counter (for the per-account histogram), the
    family-by-age sketch, and, for interactive clients, the version sketch.
    The version step does two reads of its own (is this pair already known
    today, and how many pairs are known) to enforce the per-day pair cap
    (`metrics_extended_version_pairs_per_day`).
    """
    if not ENABLED or family not in CLIENT_FAMILIES:
        return

    fam_set = family_set_key(family, day)
    pipe.sadd(fam_set, token)
    pipe.expire(fam_set, EXT_KEY_TTL_SECONDS)

    reqs = requests_key(family, day)
    pipe.hincrby(reqs, token, 1)
    pipe.expire(reqs, EXT_KEY_TTL_SECONDS)

    from sheaf.observability.usage import AGE_BUCKETS

    if account_age in AGE_BUCKETS:
        famage = family_age_key(family, account_age, day)
        pipe.pfadd(famage, token)
        pipe.expire(famage, EXT_KEY_TTL_SECONDS)

    if family not in INTERACTIVE_FAMILIES:
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


def record_request(
    method: str,
    route: str,
    status_class: str,
    client_header: str | None,
    authorization: str | None,
) -> None:
    """The route-by-family counter, from the HTTP middleware.

    The family is derived from the header the same way the auth path does
    it, with "is this an API key" read off the Authorization prefix the way
    `get_current_user` reads it, so the two never disagree. Runs for every
    request, authenticated or not, which is what "which client hits which
    route" needs.
    """
    if not ENABLED:
        return
    is_api_key = bool(authorization) and authorization.startswith("Bearer sk_")
    family = client_family_from(client_header, is_api_key=is_api_key)
    http_requests_by_client_total.labels(
        method=method,
        route=route,
        status_class=status_class,
        client_family=family,
    ).inc()


# Keep references to in-flight fire-and-forget tasks (asyncio holds them
# weakly), bounded so a stalled Redis cannot pile them up.
_bg_tasks: set[asyncio.Task] = set()
_MAX_INFLIGHT_TASKS = 256
_REDIS_OP_TIMEOUT_S = 2.0


def record_public_request(subject_type: str, system_id) -> None:
    """Count one SERVED public-profile request against its profile, today.

    Called from the public resolvers once a grant has resolved, so a 404 for
    a system that does not publish never creates a key. Synchronous and
    non-blocking (schedules a task), best-effort, never raises. The profile
    is identified by the day-salted SYSTEM token: unjoinable across days
    like everything else here, and it is a count, nothing more.
    """
    if not ENABLED or subject_type not in PUBLIC_SUBJECT_TYPES:
        return
    try:
        if len(_bg_tasks) >= _MAX_INFLIGHT_TASKS:
            return
        task = asyncio.get_running_loop().create_task(
            _record_public_request(subject_type, str(system_id))
        )
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except Exception:
        logger.debug("extended: could not schedule public request record", exc_info=True)


async def _record_public_request(subject_type: str, system_id: str) -> None:
    try:
        from sheaf.auth.sessions import get_redis
        from sheaf.observability.usage import _day_salted_token

        r = await get_redis()
        today = _today()
        key = public_requests_key(subject_type, today)
        pipe = r.pipeline()
        pipe.hincrby(key, _day_salted_token("pub", system_id, today), 1)
        pipe.expire(key, EXT_KEY_TTL_SECONDS)
        await asyncio.wait_for(pipe.execute(), timeout=_REDIS_OP_TIMEOUT_S)
    except Exception:
        logger.debug("extended: public request record failed", exc_info=True)


# ---------------------------------------------------------------------------
# Read side: gauges
# ---------------------------------------------------------------------------

# Label pairs this process has ever published per gauge, so one that drops
# out of today's data is set to 0 rather than left at yesterday's value.
_published_versions: set[tuple[str, str]] = set()
_published_patterns: set[str] = set()


def pattern_label(families: tuple[str, ...]) -> str:
    """`("web", "android")` -> `web+android`, in the fixed family order so
    the same combination always produces the same label."""
    order = {f: i for i, f in enumerate(CLIENT_FAMILIES)}
    return "+".join(sorted(families, key=lambda f: order[f]))


async def _pattern_counts(rb, day: date) -> dict[str, int]:
    """Accounts per EXACT family combination, by set algebra in Redis.

    For a combination S of families, the exact count is
    |INTER(S) minus UNION(families not in S)|. Each is one SINTERSTORE, one
    SDIFFSTORE and one SCARD on scratch keys; the tokens never leave the
    server. Families with no set today are skipped, so the number of
    combinations evaluated is 2^k - 1 for the k families actually seen, at
    most 63.
    """
    present = []
    for family in CLIENT_FAMILIES:
        if await rb.exists(family_set_key(family, day)):
            present.append(family)
    counts: dict[str, int] = {}
    if not present:
        return counts
    for size in range(1, len(present) + 1):
        for combo in itertools.combinations(present, size):
            rest = [f for f in present if f not in combo]
            inter = _scratch_key("inter")
            diff = _scratch_key("diff")
            try:
                await rb.sinterstore(inter, *[family_set_key(f, day) for f in combo])
                if rest:
                    await rb.sdiffstore(
                        diff, inter, *[family_set_key(f, day) for f in rest]
                    )
                    n = int(await rb.scard(diff))
                else:
                    n = int(await rb.scard(inter))
            finally:
                with contextlib.suppress(Exception):
                    await rb.delete(inter, diff)
            if n > 0:
                counts[pattern_label(combo)] = n
    return counts


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
        from sheaf.observability.usage import AGE_BUCKETS

        rb = await get_redis_bytes()
        today = _today()

        # Versions.
        live_versions: set[tuple[str, str]] = set()
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
            live_versions.add((family, version))
        for family, version in _published_versions - live_versions:
            active_accounts_by_version.labels(
                client_family=family, version=version
            ).set(0)
        _published_versions.update(live_versions)

        # Family by account age: a fixed, small label space, set every time.
        for family in CLIENT_FAMILIES:
            for age in AGE_BUCKETS:
                count = int(await rb.pfcount(family_age_key(family, age, today)))
                active_accounts_daily_by_family_age.labels(
                    client_family=family, account_age=age
                ).set(count)

        # Exact client combinations.
        patterns = await _pattern_counts(rb, today)
        for label, n in patterns.items():
            accounts_by_client_pattern.labels(pattern=label).set(n)
        for label in _published_patterns - set(patterns):
            accounts_by_client_pattern.labels(pattern=label).set(0)
        _published_patterns.update(patterns)
    except Exception:
        logger.debug("extended: gauge refresh failed", exc_info=True)


# ---------------------------------------------------------------------------
# Fold + sweep job
# ---------------------------------------------------------------------------


async def _fold_hash(r, key: str, hist) -> int:
    """Observe every value of one counter hash into `hist`, then delete it.

    Observe first, delete second: a crash between the two double-counts that
    hash on the next run, which is the direction to be wrong in for a
    distribution (a missed day is a hole, a doubled one is a bump).
    """
    values = await r.hvals(key)
    if not values:
        return 0
    observed = 0
    for v in values:
        try:
            hist.observe(int(v))
            observed += 1
        except (TypeError, ValueError):
            continue
    await r.delete(key)
    return observed


async def fold_daily_histograms(day: date) -> int:
    """Observe `day`'s per-token counters into the histograms, then delete
    them. Returns the number of observations made."""
    if not ENABLED:
        return 0
    from sheaf.auth.sessions import get_redis

    r = await get_redis()
    observed = 0
    for family in CLIENT_FAMILIES:
        observed += await _fold_hash(
            r, requests_key(family, day),
            account_requests_daily.labels(client_family=family),
        )
    for subject_type in PUBLIC_SUBJECT_TYPES:
        observed += await _fold_hash(
            r, public_requests_key(subject_type, day),
            public_requests_per_profile_daily.labels(subject_type=subject_type),
        )
    return observed


async def sweep_extended_keys() -> dict:
    """Fold yesterday's counters, then delete `sheaf:ext:*` keys older than
    yesterday.

    Every key already carries a 48-hour TTL; the sweep is the belt to that
    braces, so the retention promise ("nothing per account outlives two
    days") does not rest on a TTL alone. The day is the last colon-separated
    segment of every key in this namespace by construction (scratch keys
    end in a hex id, which never matches a kept day and so are swept too).
    """
    if not ENABLED:
        return {"items_processed": 0}
    from sheaf.auth.sessions import get_redis

    today = _today()
    yesterday = today - timedelta(days=1)
    observed = await fold_daily_histograms(yesterday)

    r = await get_redis()
    keep = {_day_str(today), _day_str(yesterday)}
    deleted = 0
    async for key in r.scan_iter(match=f"{KEY_PREFIX}*", count=200):
        name = key.decode() if isinstance(key, bytes) else str(key)
        if name.rsplit(":", 1)[-1] not in keep:
            await r.delete(name)
            deleted += 1
    return {
        "items_processed": deleted + observed,
        "folded": observed,
        "deleted": deleted,
    }
