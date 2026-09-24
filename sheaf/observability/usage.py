"""Privacy-respecting aggregate usage metrics (DAU / MAU).

The hard invariant: DAU/MAU are aggregate CARDINALITY only, never attributable
to an account. The mechanism is a Redis HyperLogLog (HLL) sketch per day per
scope per auth kind: account ids and system ids are PFADDed in at the auth choke
point, and only the estimated COUNT is ever published, as an id-free gauge. Raw
ids are NEVER stored, only folded into the registers.

A HLL alone is NOT membership-proof: someone holding a sketch can test whether a
KNOWN id was active by PFADDing it into a copy and watching whether the estimate
moves (an already-present id barely shifts it). So the id is never added raw - it
is first run through a keyed HMAC (`_active_token`) under the server encryption
key. Without that key an attacker cannot compute the element that was added, so a
leaked or dumped sketch answers neither "who was active" nor "was X active". The
HMAC is deterministic and collision-resistant, so the cardinality is unchanged; a
key rotation just resets the current window (acceptable for 31-day ops data).

Auth kind splits interactive client use from automation. A request authenticated
by session cookie or JWT bearer (web + native apps) is `client`; one
authenticated by an API key is `api`. They are kept in separate sketches because
a distinct count cannot be sliced out of a merged sketch after the fact. The
published `any` series is the DEDUPED union of the two (an account active both
ways in a day counts once), so it is a true total, not `client + api`.

Durability trap (why sketch bytes, not scalar counts, are persisted): Redis
survives an in-place upgrade but NOT an instance replace. A 30-day MAU is the
cardinality of the UNION of daily sketches; it cannot be reconstructed by
summing or averaging daily unique counts (that double-counts returning users).
So the per-day sketch BYTES are flushed to Postgres, and after a Redis replace a
missing day-key is RESTOREd from Postgres before the union is computed.

Everything here is best-effort: a Redis error or slowness must never fail or
meaningfully delay a request, and must never raise into the gauge pass. The
per-request PFADD is fire-and-forget; the gauges simply don't update if Redis is
down (the existing redis_up gauge covers visibility).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import itertools
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.observability.client_family import (
    CLIENT_FAMILIES,
    FAMILY_API,
    INTERACTIVE_FAMILIES,
)

logger = logging.getLogger("sheaf.metrics.usage")

# Scopes folded into their own daily sketch. "acct" = distinct accounts,
# "sys" = distinct systems.
SCOPE_ACCOUNT = "acct"
SCOPE_SYSTEM = "sys"
SCOPES = (SCOPE_ACCOUNT, SCOPE_SYSTEM)

# Auth kinds. `client` = session cookie or JWT bearer (web + native apps);
# `api` = API key (automation / integrations). Each has its own sketch. `any`
# is never stored - it is the read-time deduped union of client and api.
KIND_CLIENT = "client"
KIND_API = "api"
KIND_ANY = "any"
# Kinds that own a stored sketch (and so get PFADDed / flushed / restored).
WRITE_KINDS = (KIND_CLIENT, KIND_API)
# Kinds published as gauges (the two stored kinds plus their deduped union).
READ_KINDS = (KIND_CLIENT, KIND_API, KIND_ANY)


def _write_kinds_for(read_kind: str) -> tuple[str, ...]:
    """The stored sketch kinds that make up a published kind. `any` unions both
    stored kinds; a stored kind is just itself."""
    return WRITE_KINDS if read_kind == KIND_ANY else (read_kind,)


# Client families (web / android / ios / watch / other) split the `client` auth
# kind by platform. They are written ADDITIVELY: on an interactive request the
# `client` sketch is PFADDed exactly as before AND the family sketch is, so the
# published DAU/MAU never depend on the family sketches existing and did not
# dip when they were introduced. `api` has no family sketch of its own: the
# credential decides the family (client_family.py), so the api auth-kind sketch
# IS the api family. Family sketches are kept for the account scope only; the
# system scope is 1:1 with it and would double the keys for no extra answer.
#
# A stored sketch is identified by a "ref" (auth_kind, family), where family ""
# is the plain auth-kind sketch. Everything on the read side takes refs, so a
# published number is always "the union of these stored sketches".
FAMILY_NONE = ""
SketchRef = tuple[str, str]


def _ref_for_family(family: str) -> SketchRef:
    """The stored sketch that holds a family."""
    if family == FAMILY_API:
        return (KIND_API, FAMILY_NONE)
    return (KIND_CLIENT, family)


def _refs_for_scope(scope: str) -> list[SketchRef]:
    """Every stored sketch identity a scope can have: both auth-kind sketches,
    plus one per interactive family for the account scope."""
    refs: list[SketchRef] = [(k, FAMILY_NONE) for k in WRITE_KINDS]
    if scope == SCOPE_ACCOUNT:
        refs.extend((KIND_CLIENT, f) for f in INTERACTIVE_FAMILIES)
    return refs


def overlap_from_counts(a: int, b: int, union: int) -> int:
    """|A n B| by inclusion-exclusion: |A| + |B| - |A u B|.

    HLL gives every term, so cross-family overlap needs no per-account state at
    all. The three estimates each carry their own error, so the result is
    proportion-grade (it tells 3% from 30%, not 3% from 4%), and it is clamped
    at zero because a tiny true overlap can come out slightly negative.
    """
    return max(0, a + b - union)


# Account-age buckets: how long ago the account was created, at the time of the
# request. Four fixed buckets rather than a signup-week cohort label, which
# would grow by 52 series a year; this gives the retention shape ("are the
# people active today mostly new, or mostly long-standing?") at fixed
# cardinality. DAILY ONLY, and deliberately so: a monthly union over age
# buckets would need the persistence and restore machinery the auth-kind and
# family sketches have, and a daily gauge answers the question. So these are
# Redis day-keys with a short TTL, never persisted, never restored, and not
# part of the SketchRef scheme above.
AGE_BUCKETS: tuple[str, ...] = ("lt7d", "lt30d", "lt90d", "older")
_AGE_EDGES: tuple[tuple[timedelta, str], ...] = (
    (timedelta(days=7), "lt7d"),
    (timedelta(days=30), "lt30d"),
    (timedelta(days=90), "lt90d"),
)
# Two days: today's key is the only one ever read, and the extra day covers a
# gauge refresh that runs just after midnight reading "today" as yesterday.
AGE_KEY_TTL_SECONDS = 2 * 24 * 3600


def age_bucket(created_at: datetime | None, now: datetime | None = None) -> str | None:
    """Which age bucket an account created at `created_at` is in right now.

    Pure, so the boundaries can be tested without a clock. A naive timestamp
    is read as UTC (the column is timezone-aware, but be forgiving). A
    creation time in the future - clock skew between nodes - is treated as
    brand new rather than raising.
    """
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    age = (now or datetime.now(UTC)) - created_at
    for edge, bucket in _AGE_EDGES:
        if age < edge:
            return bucket
    return "older"


def age_day_key(scope: str, bucket: str, day: date) -> str:
    """Redis key for a scope's day-sketch of one account-age bucket, e.g.
    sheaf:hll:acct:age:lt7d:2026-09-01."""
    return f"sheaf:hll:{scope}:age:{bucket}:{_day_str(day)}"


# Day-keys live ~31 days: one past the 30-day MAU window so the trailing 30
# days are always present in Redis on a box that hasn't been replaced.
HLL_KEY_TTL_SECONDS = 31 * 24 * 3600
# The MAU lookback. 30 daily sketches unioned.
MAU_WINDOW_DAYS = 30
# Postgres sketch rows are pruned beyond this. A few days past the window so a
# restore after a Redis replace still has all 30 days even if a flush was
# missed right at the boundary.
SKETCH_RETENTION_DAYS = 35

# Process-local cache of the immutable user_id -> system_id mapping. system.user_id
# is UNIQUE (1:1), and a system id never changes for an account, so this is safe
# to cache for the process lifetime: the DB is hit at most once per account per
# process rather than on every authenticated request. Bounded so it can't grow
# without limit on a very large instance.
_system_id_cache: dict[uuid.UUID, uuid.UUID | None] = {}
_SYSTEM_ID_CACHE_CAP = 100_000

# Keep references to in-flight fire-and-forget tasks so they aren't garbage
# collected mid-flight (asyncio only holds a weak reference). Bounded: a stalled
# Redis must not let this grow without limit and exhaust the process, so once the
# cap is hit new samples are dropped (best-effort metrics, dropping is fine).
_bg_tasks: set[asyncio.Task] = set()
_MAX_INFLIGHT_TASKS = 256
# Hard ceiling on a single PFADD round-trip, so a wedged Redis connection cannot
# pin a task (and its slot) open indefinitely.
_REDIS_OP_TIMEOUT_S = 2.0


def _active_token(scope: str, value: str) -> str:
    """Keyed HMAC of an id, so what lands in a sketch cannot be reconstructed or
    membership-tested without the server key. Deterministic and
    collision-resistant, so distinct ids stay distinct and the cardinality is
    exactly preserved. Keyed on the encryption key (a stable server secret);
    rotating it resets the current window's sketches, which is fine for 31-day
    ops data."""
    from sheaf.config import settings

    return hmac.new(
        settings.get_encryption_key(),
        f"{scope}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _day_salted_token(scope: str, value: str, day: date) -> str:
    """`_active_token` with the day folded in, for the extended tier.

    The stable token above is fine inside a HyperLogLog, whose members are
    never read back. The extended tier holds per-account state that in
    principle could be (a counter keyed by token, say), and the salt is what
    makes that state an ephemeral aggregate rather than a pseudonymised
    activity log: a token seen on day N cannot be joined to day N+1, so
    nobody holding two days of keys can line an account up across them.
    """
    from sheaf.config import settings

    return hmac.new(
        settings.get_encryption_key(),
        f"{scope}:{_day_str(day)}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _day_str(day: date) -> str:
    return day.isoformat()


def day_key(scope: str, auth_kind: str, day: date) -> str:
    """Redis key for a scope's day-sketch of one auth kind, e.g.
    sheaf:hll:acct:client:2026-09-01."""
    return f"sheaf:hll:{scope}:{auth_kind}:{_day_str(day)}"


def family_day_key(scope: str, family: str, day: date) -> str:
    """Redis key for a scope's day-sketch of one interactive client family, e.g.
    sheaf:hll:acct:fam:web:2026-09-01. The `fam:` segment keeps the family
    namespace apart from the auth-kind keys so neither can shadow the other."""
    return f"sheaf:hll:{scope}:fam:{family}:{_day_str(day)}"


def _ref_key(scope: str, ref: SketchRef, day: date) -> str:
    auth_kind, family = ref
    if family == FAMILY_NONE:
        return day_key(scope, auth_kind, day)
    return family_day_key(scope, family, day)


def _today() -> date:
    return datetime.now(UTC).date()


def _recent_days(n: int) -> list[date]:
    """The trailing `n` days, most recent first (today, yesterday, ...)."""
    today = _today()
    return [today - timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------------------
# Write side: PFADD at the auth choke point (fire-and-forget)
# ---------------------------------------------------------------------------


def record_active_account(
    user_id: uuid.UUID,
    auth_kind: str,
    client_family: str | None = None,
    account_age: str | None = None,
    client_header: str | None = None,
) -> None:
    """Record an authenticated account (and its system) as active today, under
    the given auth kind (`client` or `api`), for an interactive request under
    its client family as well, and under its account-age bucket.

    Called from the auth dependency once a request has authenticated. Synchronous
    and non-blocking: it only schedules a background task and returns immediately,
    so neither Redis latency nor a Redis outage can delay or fail the request.
    Any error inside the task is swallowed - usage metrics are strictly
    best-effort.
    """
    try:
        from sheaf.config import settings

        if not settings.metrics_enabled:
            return
        if auth_kind not in WRITE_KINDS:
            return
        if len(_bg_tasks) >= _MAX_INFLIGHT_TASKS:
            # Redis is not draining tasks fast enough (or is stalled); drop this
            # sample rather than let the backlog grow without bound.
            logger.debug("usage: in-flight task cap hit, dropping activity sample")
            return
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            _record_active(
                user_id, auth_kind, client_family, account_age, client_header
            )
        )
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except Exception:
        # Strictly best-effort: scheduling the metric write must never fail or
        # delay auth. A missing running loop, or anything else, is swallowed.
        logger.debug("usage: could not schedule activity record", exc_info=True)


async def _record_active(
    user_id: uuid.UUID,
    auth_kind: str,
    client_family: str | None,
    account_age: str | None = None,
    client_header: str | None = None,
) -> None:
    """Fire-and-forget body: PFADD the account id into today's acct sketch and
    the system id into today's sys sketch for this auth kind, refreshing the
    ~31-day TTL, plus the account id into today's family sketch when the
    request came from an interactive client. Uses the existing shared Redis
    client. Never raises."""
    try:
        from sheaf.auth.sessions import get_redis

        r = await get_redis()
        today = _today()

        system_id = await _system_id_for(user_id)

        pipe = r.pipeline()
        acct_key = day_key(SCOPE_ACCOUNT, auth_kind, today)
        pipe.pfadd(acct_key, _active_token(SCOPE_ACCOUNT, str(user_id)))
        pipe.expire(acct_key, HLL_KEY_TTL_SECONDS)
        if system_id is not None:
            sys_key = day_key(SCOPE_SYSTEM, auth_kind, today)
            pipe.pfadd(sys_key, _active_token(SCOPE_SYSTEM, str(system_id)))
            pipe.expire(sys_key, HLL_KEY_TTL_SECONDS)
        # The family sketch, alongside (not instead of) the auth-kind one. The
        # membership check is the guard against a caller passing something
        # unbounded: only a known interactive family ever becomes a key.
        if auth_kind == KIND_CLIENT and client_family in INTERACTIVE_FAMILIES:
            fam_key = family_day_key(SCOPE_ACCOUNT, client_family, today)
            pipe.pfadd(fam_key, _active_token(SCOPE_ACCOUNT, str(user_id)))
            pipe.expire(fam_key, HLL_KEY_TTL_SECONDS)
        # Extended tier, for every family including `api`: the per-version
        # sketch, the family token set, the per-token request counter and
        # the family-by-age sketch, all under the DAY-SALTED token. A no-op
        # inside unless METRICS_EXTENDED is on; the raw header goes in, only
        # a bounded family and version come out.
        if client_family is not None:
            from sheaf.observability import extended

            await asyncio.wait_for(
                extended.record_activity(
                    r,
                    pipe,
                    client_family,
                    client_header,
                    account_age,
                    today,
                    _day_salted_token(SCOPE_ACCOUNT, str(user_id), today),
                ),
                timeout=_REDIS_OP_TIMEOUT_S,
            )
        # The age bucket, for every auth kind: an automation account has an
        # age too. Same membership guard, same token, a shorter TTL because
        # only today's key is ever read.
        if account_age in AGE_BUCKETS:
            age_key = age_day_key(SCOPE_ACCOUNT, account_age, today)
            pipe.pfadd(age_key, _active_token(SCOPE_ACCOUNT, str(user_id)))
            pipe.expire(age_key, AGE_KEY_TTL_SECONDS)
        await asyncio.wait_for(pipe.execute(), timeout=_REDIS_OP_TIMEOUT_S)
    except Exception:
        # Best-effort: a down or slow Redis just means this activity isn't
        # counted. Debug-level so a Redis blip doesn't spam warnings on every
        # request.
        logger.debug("usage: PFADD failed for account activity", exc_info=True)


async def _system_id_for(user_id: uuid.UUID) -> uuid.UUID | None:
    """Resolve the (immutable, 1:1) system id for an account, cached per process.

    On a cache miss, one indexed scalar query on a fresh short-lived session -
    not the request's session, which may already be closed by the time this
    background task runs. Caches the miss result (including None) so a lookup
    happens at most once per account per process.
    """
    if user_id in _system_id_cache:
        return _system_id_cache[user_id]

    from sheaf.database import async_session_factory
    from sheaf.models.system import System

    async with async_session_factory() as db:
        system_id = await db.scalar(
            select(System.id).where(System.user_id == user_id)
        )

    if len(_system_id_cache) >= _SYSTEM_ID_CACHE_CAP:
        _system_id_cache.clear()
    _system_id_cache[user_id] = system_id
    return system_id


# ---------------------------------------------------------------------------
# Durability flush: persist the day-sketch BYTES to Postgres
# ---------------------------------------------------------------------------


async def flush_day_sketches(db: AsyncSession) -> dict:
    """Persist each live day-sketch's raw HLL bytes into usage_daily_sketches,
    and prune rows beyond the retention window.

    Reads the raw sketch bytes off each recent (day, scope, auth kind) key in
    Redis (bytes-mode client - the registers are binary, not UTF-8) and UPSERTs
    them keyed by (day, scope, auth_kind). This is the durability backstop: after
    a Redis instance replace wipes the day-keys, the monthly union restores them
    from here. Only the aggregate registers are stored, nothing per-account, so
    this table is ops data and stays out of the user data export.

    Runs periodically; the delta lost to a Redis replace is bounded by the flush
    interval.
    """
    from sheaf.models.usage_sketch import UsageDailySketch

    flushed = 0
    try:
        from sheaf.auth.sessions import get_redis_bytes

        rb = await get_redis_bytes()
        await rb.ping()
    except Exception:
        # Redis unreachable: nothing to flush this tick. The prune below still
        # needs the DB, which is independent of Redis, so fall through to it.
        rb = None

    if rb is not None:
        # Only days still inside the TTL window can have a live key. Iterate the
        # candidate day/scope/kind triples and flush any that currently exist.
        for day in _recent_days(SKETCH_RETENTION_DAYS):
            for scope in SCOPES:
                for ref in _refs_for_scope(scope):
                    auth_kind, family = ref
                    try:
                        raw = await rb.get(_ref_key(scope, ref, day))
                    except Exception:
                        logger.debug(
                            "usage: reading sketch bytes failed for %s %s %s %s",
                            scope, auth_kind, family or "-", day, exc_info=True,
                        )
                        continue
                    if raw is None:
                        continue
                    stmt = (
                        pg_insert(UsageDailySketch)
                        .values(
                            day=day,
                            scope=scope,
                            auth_kind=auth_kind,
                            client_family=family,
                            sketch=raw,
                            updated_at=datetime.now(UTC),
                        )
                        .on_conflict_do_update(
                            index_elements=[
                                "day", "scope", "auth_kind", "client_family",
                            ],
                            set_={"sketch": raw, "updated_at": datetime.now(UTC)},
                        )
                    )
                    await db.execute(stmt)
                    flushed += 1

    # Prune sketches beyond the retention window so the table stays bounded.
    from sqlalchemy import delete as sa_delete

    cutoff = _today() - timedelta(days=SKETCH_RETENTION_DAYS)
    await db.execute(
        sa_delete(UsageDailySketch).where(UsageDailySketch.day < cutoff)
    )
    await db.commit()

    return {"items_processed": flushed}


# ---------------------------------------------------------------------------
# Read side: daily count + monthly union (with restore-from-Postgres)
# ---------------------------------------------------------------------------


async def _merge_count(rb, keys: list[str]) -> int:
    """Distinct-id cardinality across one or more sketch keys. A single key is a
    plain PFCOUNT; several are PFMERGEd into a scratch key first (the union),
    then PFCOUNTed and the scratch removed. Missing keys count as empty."""
    if not keys:
        return 0
    if len(keys) == 1:
        return int(await rb.pfcount(keys[0]))
    scratch = f"sheaf:hll:scratch:{uuid.uuid4().hex}"
    try:
        await rb.pfmerge(scratch, *keys)
        return int(await rb.pfcount(scratch))
    finally:
        with contextlib.suppress(Exception):
            await rb.delete(scratch)


async def daily_count(scope: str, read_kind: str) -> int | None:
    """Estimated distinct-id cardinality for today's sketch of a published kind.

    `client` / `api` PFCOUNT their own key; `any` PFMERGEs today's client and
    api keys so an id active both ways counts once. Returns None if Redis is
    unreachable, so the caller can leave the gauge at its last value rather than
    zeroing it.
    """
    try:
        from sheaf.auth.sessions import get_redis_bytes

        rb = await get_redis_bytes()
        today = _today()
        keys = [day_key(scope, wk, today) for wk in _write_kinds_for(read_kind)]
        return await _merge_count(rb, keys)
    except Exception:
        logger.debug(
            "usage: daily count failed for %s/%s", scope, read_kind, exc_info=True
        )
        return None


async def monthly_count(db: AsyncSession, scope: str, read_kind: str) -> int | None:
    """Estimated distinct-id cardinality over the trailing 30 days for a
    published kind.

    MAU is the cardinality of the UNION of the daily sketches (PFMERGE then
    PFCOUNT), NOT the sum of daily counts - summing double-counts anyone active
    on more than one day. `any` unions both stored kinds across all 30 days so
    an id active either way in the window counts once. Where a day-key is MISSING
    from Redis (the post-replace case), its persisted sketch is RESTOREd from
    Postgres into a scratch key so it still participates in the union. This
    restore path is the entire reason the sketch bytes are persisted: without it
    a Redis replace would silently drop up to 30 days of history from MAU.

    Returns None if Redis is unreachable.
    """
    refs = [(k, FAMILY_NONE) for k in _write_kinds_for(read_kind)]
    return await _monthly_union(db, scope, refs)


async def family_daily_count(family: str) -> int | None:
    """Estimated distinct accounts active today on one client family."""
    try:
        from sheaf.auth.sessions import get_redis_bytes

        rb = await get_redis_bytes()
        key = _ref_key(SCOPE_ACCOUNT, _ref_for_family(family), _today())
        return await _merge_count(rb, [key])
    except Exception:
        logger.debug("usage: family daily count failed for %s", family, exc_info=True)
        return None


async def age_daily_count(bucket: str) -> int | None:
    """Estimated distinct accounts active today whose account is in one age
    bucket. Daily only; see AGE_BUCKETS for why there is no monthly twin."""
    try:
        from sheaf.auth.sessions import get_redis_bytes

        rb = await get_redis_bytes()
        return await _merge_count(rb, [age_day_key(SCOPE_ACCOUNT, bucket, _today())])
    except Exception:
        logger.debug("usage: age daily count failed for %s", bucket, exc_info=True)
        return None


async def family_monthly_count(db: AsyncSession, family: str) -> int | None:
    """Estimated distinct accounts active on one client family over the trailing
    30 days: the union of that family's daily sketches, restore path included."""
    return await _monthly_union(db, SCOPE_ACCOUNT, [_ref_for_family(family)])


async def family_monthly_overlap(db: AsyncSession, a: str, b: str) -> int | None:
    """Estimated distinct accounts active on BOTH families over the trailing 30
    days. See `overlap_from_counts` for why this needs no per-account state."""
    count_a = await family_monthly_count(db, a)
    count_b = await family_monthly_count(db, b)
    union = await _monthly_union(
        db, SCOPE_ACCOUNT, [_ref_for_family(a), _ref_for_family(b)]
    )
    if count_a is None or count_b is None or union is None:
        return None
    return overlap_from_counts(count_a, count_b, union)


async def _monthly_union(
    db: AsyncSession, scope: str, refs: list[SketchRef]
) -> int | None:
    """Cardinality of the union of the given stored sketches over the trailing
    30 days, restoring any day missing from Redis out of Postgres first. The
    one implementation behind MAU, the per-family monthly counts, and the
    pairwise overlaps, so they cannot disagree about what "the last 30 days"
    means. Returns None if Redis is unreachable."""
    try:
        from sheaf.auth.sessions import get_redis_bytes

        rb = await get_redis_bytes()
        await rb.ping()
    except Exception:
        logger.debug("usage: monthly union skipped, Redis down", exc_info=True)
        return None

    scratch = f"sheaf:hll:scratch:{uuid.uuid4().hex}"
    # Temp keys we RESTOREd from Postgres for this computation; cleaned up after.
    restored_keys: list[str] = []
    # Keys to feed into the union: live day-keys as-is, plus any restored ones.
    source_keys: list[str] = []

    try:
        days = _recent_days(MAU_WINDOW_DAYS)

        for ref in refs:
            # Which day-keys for this stored sketch are present in Redis right now.
            present: dict[date, bool] = {}
            for day in days:
                try:
                    present[day] = bool(await rb.exists(_ref_key(scope, ref, day)))
                except Exception:
                    present[day] = False

            missing_days = [d for d in days if not present[d]]

            # Restore missing days from the persisted Postgres sketches. This is
            # the post-Redis-replace recovery: without it the union would
            # silently omit every day whose key Redis lost.
            if missing_days:
                restored = await _load_persisted_sketches(db, scope, ref, missing_days)
                for day, raw in restored.items():
                    tmp = f"sheaf:hll:restore:{uuid.uuid4().hex}"
                    try:
                        # SET the raw register bytes back into Redis as a string;
                        # PFMERGE/PFCOUNT then treat it as a normal sketch again.
                        await rb.set(tmp, raw, ex=3600)
                        restored_keys.append(tmp)
                        source_keys.append(tmp)
                    except Exception:
                        logger.debug(
                            "usage: restoring persisted sketch failed for %s %s %s",
                            scope, ref, day, exc_info=True,
                        )

            # Live day-keys for this stored sketch.
            for day in days:
                if present[day]:
                    source_keys.append(_ref_key(scope, ref, day))

        if not source_keys:
            return 0

        await rb.pfmerge(scratch, *source_keys)
        return int(await rb.pfcount(scratch))
    except Exception:
        logger.debug(
            "usage: monthly union failed for %s/%s", scope, refs, exc_info=True
        )
        return None
    finally:
        # Never leave scratch/restore keys behind. Best-effort; they also carry
        # a short TTL as a backstop.
        try:
            to_delete = [scratch, *restored_keys]
            if to_delete:
                await rb.delete(*to_delete)
        except Exception:
            pass


async def _load_persisted_sketches(
    db: AsyncSession, scope: str, ref: SketchRef, days: list[date]
) -> dict[date, bytes]:
    """Fetch persisted sketch bytes for the given (scope, ref, days) from
    Postgres."""
    from sheaf.models.usage_sketch import UsageDailySketch

    if not days:
        return {}
    auth_kind, family = ref
    result = await db.execute(
        select(UsageDailySketch.day, UsageDailySketch.sketch).where(
            UsageDailySketch.scope == scope,
            UsageDailySketch.auth_kind == auth_kind,
            UsageDailySketch.client_family == family,
            UsageDailySketch.day.in_(days),
        )
    )
    out: dict[date, bytes] = {}
    for row_day, row_sketch in result.all():
        if row_sketch is not None:
            out[row_day] = bytes(row_sketch)
    return out


# ---------------------------------------------------------------------------
# Gauge refresh (called from the slow gauge pass)
# ---------------------------------------------------------------------------


async def refresh_usage_gauges(db: AsyncSession) -> None:
    """Set the id-free DAU/MAU cardinality gauges, one series per auth kind
    (client / api / any).

    Best-effort: if a value comes back None (Redis down), the corresponding
    gauge is left at its previous value rather than zeroed - a Redis blip should
    not read as "activity dropped to zero".
    """
    from sheaf.observability.metrics import (
        active_accounts_daily,
        active_accounts_daily_by_age,
        active_accounts_daily_by_client,
        active_accounts_monthly,
        active_accounts_monthly_by_client,
        active_accounts_monthly_overlap,
        active_systems_daily,
        active_systems_monthly,
    )

    # Per-family share, then every pairwise overlap from the monthly counts
    # just computed plus one union per pair: 6 + 15 unions rather than the 45
    # that calling family_monthly_overlap for each pair would cost.
    fam_monthly: dict[str, int] = {}
    for family in CLIENT_FAMILIES:
        fam_daily = await family_daily_count(family)
        if fam_daily is not None:
            active_accounts_daily_by_client.labels(client_family=family).set(fam_daily)
        monthly = await family_monthly_count(db, family)
        if monthly is not None:
            fam_monthly[family] = monthly
            active_accounts_monthly_by_client.labels(client_family=family).set(monthly)
    for bucket in AGE_BUCKETS:
        by_age = await age_daily_count(bucket)
        if by_age is not None:
            active_accounts_daily_by_age.labels(account_age=bucket).set(by_age)

    for a, b in itertools.combinations(CLIENT_FAMILIES, 2):
        if a not in fam_monthly or b not in fam_monthly:
            continue
        union = await _monthly_union(
            db, SCOPE_ACCOUNT, [_ref_for_family(a), _ref_for_family(b)]
        )
        if union is None:
            continue
        active_accounts_monthly_overlap.labels(families=f"{a}+{b}").set(
            overlap_from_counts(fam_monthly[a], fam_monthly[b], union)
        )

    for read_kind in READ_KINDS:
        acct_daily = await daily_count(SCOPE_ACCOUNT, read_kind)
        if acct_daily is not None:
            active_accounts_daily.labels(auth_kind=read_kind).set(acct_daily)

        sys_daily = await daily_count(SCOPE_SYSTEM, read_kind)
        if sys_daily is not None:
            active_systems_daily.labels(auth_kind=read_kind).set(sys_daily)

        acct_monthly = await monthly_count(db, SCOPE_ACCOUNT, read_kind)
        if acct_monthly is not None:
            active_accounts_monthly.labels(auth_kind=read_kind).set(acct_monthly)

        sys_monthly = await monthly_count(db, SCOPE_SYSTEM, read_kind)
        if sys_monthly is not None:
            active_systems_monthly.labels(auth_kind=read_kind).set(sys_monthly)
