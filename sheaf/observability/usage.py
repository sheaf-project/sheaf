"""Privacy-respecting aggregate usage metrics (DAU / MAU).

The hard invariant: DAU/MAU are aggregate CARDINALITY only, never attributable
to an account. The mechanism is a Redis HyperLogLog (HLL) sketch per day per
scope per auth kind: account ids and system ids are PFADDed in at the auth choke
point, and only the estimated COUNT is ever published, as an id-free gauge. HLL
is one-way - you cannot enumerate members or answer "was account X active on day
Y" from a sketch - so raw ids are NEVER stored anywhere, only folded into the
registers.

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
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

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
# collected mid-flight (asyncio only holds a weak reference).
_bg_tasks: set[asyncio.Task] = set()


def _day_str(day: date) -> str:
    return day.isoformat()


def day_key(scope: str, auth_kind: str, day: date) -> str:
    """Redis key for a scope's day-sketch of one auth kind, e.g.
    sheaf:hll:acct:client:2026-09-01."""
    return f"sheaf:hll:{scope}:{auth_kind}:{_day_str(day)}"


def _today() -> date:
    return datetime.now(UTC).date()


def _recent_days(n: int) -> list[date]:
    """The trailing `n` days, most recent first (today, yesterday, ...)."""
    today = _today()
    return [today - timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------------------
# Write side: PFADD at the auth choke point (fire-and-forget)
# ---------------------------------------------------------------------------


def record_active_account(user_id: uuid.UUID, auth_kind: str) -> None:
    """Record an authenticated account (and its system) as active today, under
    the given auth kind (`client` or `api`).

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
        loop = asyncio.get_running_loop()
        task = loop.create_task(_record_active(user_id, auth_kind))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except Exception:
        # Strictly best-effort: scheduling the metric write must never fail or
        # delay auth. A missing running loop, or anything else, is swallowed.
        logger.debug("usage: could not schedule activity record", exc_info=True)


async def _record_active(user_id: uuid.UUID, auth_kind: str) -> None:
    """Fire-and-forget body: PFADD the account id into today's acct sketch and
    the system id into today's sys sketch for this auth kind, refreshing the
    ~31-day TTL. Uses the existing shared Redis client. Never raises."""
    try:
        from sheaf.auth.sessions import get_redis

        r = await get_redis()
        today = _today()

        system_id = await _system_id_for(user_id)

        pipe = r.pipeline()
        acct_key = day_key(SCOPE_ACCOUNT, auth_kind, today)
        pipe.pfadd(acct_key, str(user_id))
        pipe.expire(acct_key, HLL_KEY_TTL_SECONDS)
        if system_id is not None:
            sys_key = day_key(SCOPE_SYSTEM, auth_kind, today)
            pipe.pfadd(sys_key, str(system_id))
            pipe.expire(sys_key, HLL_KEY_TTL_SECONDS)
        await pipe.execute()
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
                for auth_kind in WRITE_KINDS:
                    try:
                        raw = await rb.get(day_key(scope, auth_kind, day))
                    except Exception:
                        logger.debug(
                            "usage: reading sketch bytes failed for %s %s %s",
                            scope, auth_kind, day, exc_info=True,
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
                            sketch=raw,
                            updated_at=datetime.now(UTC),
                        )
                        .on_conflict_do_update(
                            index_elements=["day", "scope", "auth_kind"],
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

        for write_kind in _write_kinds_for(read_kind):
            # Which day-keys for this stored kind are present in Redis right now.
            present: dict[date, bool] = {}
            for day in days:
                try:
                    present[day] = bool(
                        await rb.exists(day_key(scope, write_kind, day))
                    )
                except Exception:
                    present[day] = False

            missing_days = [d for d in days if not present[d]]

            # Restore missing days from the persisted Postgres sketches. This is
            # the post-Redis-replace recovery: without it the union would
            # silently omit every day whose key Redis lost.
            if missing_days:
                restored = await _load_persisted_sketches(
                    db, scope, write_kind, missing_days
                )
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
                            scope, write_kind, day, exc_info=True,
                        )

            # Live day-keys for this stored kind.
            for day in days:
                if present[day]:
                    source_keys.append(day_key(scope, write_kind, day))

        if not source_keys:
            return 0

        await rb.pfmerge(scratch, *source_keys)
        return int(await rb.pfcount(scratch))
    except Exception:
        logger.debug(
            "usage: monthly union failed for %s/%s", scope, read_kind, exc_info=True
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
    db: AsyncSession, scope: str, auth_kind: str, days: list[date]
) -> dict[date, bytes]:
    """Fetch persisted sketch bytes for the given (scope, auth_kind, days) from
    Postgres."""
    from sheaf.models.usage_sketch import UsageDailySketch

    if not days:
        return {}
    result = await db.execute(
        select(UsageDailySketch.day, UsageDailySketch.sketch).where(
            UsageDailySketch.scope == scope,
            UsageDailySketch.auth_kind == auth_kind,
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
        active_accounts_monthly,
        active_systems_daily,
        active_systems_monthly,
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
