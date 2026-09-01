"""Host-side tests for the aggregate usage (DAU/MAU) HLL machinery.

The load-bearing correctness properties:

* MAU is the cardinality of the UNION of the daily sketches, NOT the sum of
  daily counts - a returning user active on two days must be counted once.
* When a day-key is missing from Redis (the post-instance-replace case), the
  monthly union RESTOREs its persisted sketch from Postgres, so MAU stays
  accurate across a Redis replace. This is the entire reason the sketch BYTES
  are persisted rather than a scalar count.

These run host-side against the test stack's Redis (SHEAF_TEST_REDIS_URL) and
Postgres (SHEAF_TEST_DB_URL). Each test uses a unique throwaway `scope` string
so it never collides with the app's real acct/sys sketches or a parallel run.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from sheaf.observability import usage
from sheaf.observability.usage import day_key, monthly_count

REDIS_URL = os.environ.get("SHEAF_TEST_REDIS_URL", "redis://localhost:6380/0")


def _db_url() -> str:
    from sheaf.config import settings

    return os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url


def _bytes_client() -> aioredis.Redis:
    return aioredis.from_url(REDIS_URL, decode_responses=False)


def _patch_redis_bytes(monkeypatch, client: aioredis.Redis) -> None:
    """Point usage.get_redis_bytes (resolved lazily inside usage.py) at a client
    built from the host-visible test Redis URL, since settings.redis_url is the
    in-container hostname."""

    async def _fake_get_redis_bytes() -> aioredis.Redis:
        return client

    import sheaf.auth.sessions as sessions

    monkeypatch.setattr(sessions, "get_redis_bytes", _fake_get_redis_bytes)


async def _session() -> tuple:
    engine = create_async_engine(_db_url())
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, maker


def test_mau_is_the_union_not_the_sum(monkeypatch):
    """Two overlapping daily sketches must union to the deduped cardinality, not
    the sum of the two daily counts."""
    # Throwaway scope, distinct from the real "acct"/"sys" and unique per run.
    # Must fit the scope column (String(8)), which is deliberately tight because
    # production only ever stores "acct" / "sys".
    scope = f"t{uuid.uuid4().hex[:7]}"
    rb = _bytes_client()
    _patch_redis_bytes(monkeypatch, rb)

    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    k_today = day_key(scope, today)
    k_yesterday = day_key(scope, yesterday)

    # Day A: ids 0..799. Day B: ids 400..1199. Overlap 400..799 (400 shared).
    # Distinct union = 1200; naive sum of daily counts = 800 + 800 = 1600.
    ids_a = [f"id-{i}" for i in range(0, 800)]
    ids_b = [f"id-{i}" for i in range(400, 1200)]

    async def body() -> int:
        engine, maker = await _session()
        try:
            await rb.delete(k_today, k_yesterday)
            await rb.execute_command("PFADD", k_today, *ids_a)
            await rb.execute_command("PFADD", k_yesterday, *ids_b)
            daily_a = int(await rb.pfcount(k_today))
            daily_b = int(await rb.pfcount(k_yesterday))
            async with maker() as db:
                mau = await monthly_count(db, scope)
            # Both daily counts should be ~800; the naive sum ~1600.
            assert 760 <= daily_a <= 840, daily_a
            assert 760 <= daily_b <= 840, daily_b
            return mau
        finally:
            await rb.delete(k_today, k_yesterday)
            await rb.aclose()
            await engine.dispose()

    mau = asyncio.run(body())
    # Union is ~1200 (within HLL error), and unmistakably below the 1600 sum.
    assert mau is not None
    assert 1140 <= mau <= 1260, f"MAU union off: {mau}"
    assert mau < 1400, f"MAU looks like a SUM ({mau}), not a union"


def test_mau_restores_a_missing_day_from_postgres(monkeypatch):
    """Simulate a Redis instance replace: a day's sketch survives only in
    Postgres (its Redis key is gone). The monthly union must RESTORE it and
    still reflect those ids, otherwise a replace silently drops history."""
    # Throwaway scope, distinct from the real "acct"/"sys" and unique per run.
    # Must fit the scope column (String(8)), which is deliberately tight because
    # production only ever stores "acct" / "sys".
    scope = f"t{uuid.uuid4().hex[:7]}"
    rb = _bytes_client()
    _patch_redis_bytes(monkeypatch, rb)

    today = datetime.now(UTC).date()
    lost_day = today - timedelta(days=5)
    k_today = day_key(scope, today)
    k_lost = day_key(scope, lost_day)

    ids_today = [f"t-{i}" for i in range(0, 300)]
    ids_lost = [f"l-{i}" for i in range(0, 500)]  # disjoint from today's set

    async def body() -> tuple[int, int]:
        from sheaf.models.usage_sketch import UsageDailySketch

        engine, maker = await _session()
        try:
            await rb.delete(k_today, k_lost)

            # Build the "lost" day's sketch in Redis, capture its raw bytes as
            # the flush job would have persisted, then DELETE the Redis key to
            # emulate the post-replace state where only Postgres has it.
            await rb.execute_command("PFADD", k_lost, *ids_lost)
            lost_bytes = await rb.get(k_lost)
            await rb.delete(k_lost)

            # Today's key is live in Redis.
            await rb.execute_command("PFADD", k_today, *ids_today)

            async with maker() as db:
                # Clean any stray rows for this throwaway scope, then persist the
                # lost day's sketch exactly as flush_day_sketches would have.
                await db.execute(
                    delete(UsageDailySketch).where(UsageDailySketch.scope == scope)
                )
                db.add(
                    UsageDailySketch(
                        day=lost_day,
                        scope=scope,
                        sketch=lost_bytes,
                        updated_at=datetime.now(UTC),
                    )
                )
                await db.commit()

                # Without restore this would be ~300 (today only). With restore
                # it must be ~800 (today's 300 + the lost day's 500, disjoint).
                mau = await monthly_count(db, scope)

                # And a control: with the persisted row removed, the same union
                # sees only today's live key.
                await db.execute(
                    delete(UsageDailySketch).where(UsageDailySketch.scope == scope)
                )
                await db.commit()
                mau_without = await monthly_count(db, scope)

            return mau, mau_without
        finally:
            await rb.delete(k_today, k_lost)
            async with maker() as db:
                await db.execute(
                    delete(UsageDailySketch).where(UsageDailySketch.scope == scope)
                )
                await db.commit()
            await rb.aclose()
            await engine.dispose()

    mau, mau_without = asyncio.run(body())
    assert mau is not None and mau_without is not None
    # Restored: today (300) unioned with the recovered lost day (500) = ~800.
    assert 760 <= mau <= 840, f"restore path did not recover the lost day: {mau}"
    # Control: no persisted row, so only today's live key contributes (~300).
    assert 280 <= mau_without <= 320, mau_without
    assert mau > mau_without + 300, (
        f"restore added no meaningful cardinality: {mau} vs {mau_without}"
    )


def test_day_key_scheme_is_id_free_and_dated():
    """The key scheme is scope + ISO day only; never an account/system id."""
    from datetime import date

    key = day_key(usage.SCOPE_ACCOUNT, date(2026, 9, 1))
    assert key == "sheaf:hll:acct:2026-09-01"
    key_sys = day_key(usage.SCOPE_SYSTEM, date(2026, 9, 1))
    assert key_sys == "sheaf:hll:sys:2026-09-01"
