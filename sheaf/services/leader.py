"""Postgres advisory-lock leader election for the background loops.

Every app replica runs the API; exactly one should run the background
work (job registry, notification dispatcher, import runner). A replica
that starts the loops without coordination would double-fire reminders,
race the per-job scheduling reads, and duplicate housekeeping sweeps.

The election is a session-scoped `pg_try_advisory_lock` on a dedicated
connection held open for the leader's lifetime:

- Acquisition is instant and uncontended on a single-instance deploy,
  so the default selfhost experience is unchanged.
- The lock dies with the connection. If the leader crashes, loses its
  DB connection, or is killed, Postgres releases the lock and a standby
  acquires it on its next retry (a few seconds of failover).
- A heartbeat (`SELECT 1` on the lock-holding connection) detects a
  silently-dead connection from the leader's own side; the loops are
  cancelled and the replica rejoins the election rather than running
  unlocked.

The per-item claim layers stay in place underneath (outbox rows,
import jobs, and export jobs all claim with FOR UPDATE SKIP LOCKED +
lease reclaim), so a brief overlap during failover is safe - the
election prevents sustained duplication, the claims prevent double
processing. This also keeps the later move to fully claim-based
multi-worker scheduling small: only the registry scheduler depends on
leadership.

`LEADER_ELECTION=false` restores the old run-everywhere behaviour as an
operator escape hatch.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy import text

logger = logging.getLogger("sheaf.leader")

# Stable application-wide lock key. Arbitrary but must never change, or
# two app versions deployed side by side would both think they lead.
LEADER_LOCK_KEY = 0x53484541_46_01  # "SHEAF" + namespace 01: background loops

_RETRY_SECONDS = 5.0
_HEARTBEAT_SECONDS = 10.0

LoopFactory = Callable[[], Coroutine[Any, Any, None]]


async def leader_loop(
    loops: list[tuple[str, LoopFactory]],
    *,
    lock_key: int = LEADER_LOCK_KEY,
) -> None:
    """Compete for leadership; run `loops` while holding it.

    Runs forever (until cancelled). Holds one connection from the main
    pool while leading or probing - acceptable against the pool budget,
    and using the main engine keeps connection settings/observability
    consistent.
    """
    from sheaf.database import engine
    from sheaf.observability.metrics import (
        leader_is_leader,
        leader_transitions_total,
    )

    # Publish a baseline immediately so every process exposes the gauge
    # (standbys at 0). With multiprocess_mode=livesum, sum() across live
    # processes is then the leader count from the first scrape onward.
    leader_is_leader.set(0)

    while True:
        try:
            async with engine.connect() as conn:
                got = (
                    await conn.execute(
                        text("SELECT pg_try_advisory_lock(:key)"),
                        {"key": lock_key},
                    )
                ).scalar()
                if not got:
                    # Someone else leads. The held connection goes back to
                    # the pool while we wait out the retry interval.
                    leader_is_leader.set(0)
                else:
                    leader_transitions_total.inc()
                    leader_is_leader.set(1)
                    logger.info(
                        "Leadership acquired; starting %d background loops",
                        len(loops),
                    )
                    tasks = [
                        asyncio.create_task(factory(), name=f"leader-{name}")
                        for name, factory in loops
                    ]
                    try:
                        while True:
                            await asyncio.sleep(_HEARTBEAT_SECONDS)
                            # Liveness probe on the lock-holding connection.
                            # If Postgres went away, this raises, we drop to
                            # the finally, and rejoin the election - the
                            # lock is already gone server-side with the
                            # dead session.
                            await conn.execute(text("SELECT 1"))
                    finally:
                        leader_is_leader.set(0)
                        logger.info("Standing down; stopping background loops")
                        for t in tasks:
                            t.cancel()
                        with contextlib.suppress(Exception):
                            await asyncio.gather(*tasks, return_exceptions=True)
                        # Discard the underlying DBAPI connection rather than
                        # returning it to the pool: if it is somehow still
                        # alive, a pooled idle connection would keep holding
                        # the advisory lock and wedge the election until the
                        # pool recycled it. Invalidate guarantees the lock
                        # dies with the connection.
                        with contextlib.suppress(Exception):
                            await conn.invalidate()
        except asyncio.CancelledError:
            # Shutdown: stop counting this process toward the leader sum.
            with contextlib.suppress(Exception):
                leader_is_leader.set(0)
            raise
        except Exception:
            # Lost the lock connection mid-lease: we are no longer leader
            # until the next acquisition, so reflect that before retrying.
            with contextlib.suppress(Exception):
                leader_is_leader.set(0)
            logger.exception(
                "Leader election connection error; retrying in %ss",
                _RETRY_SECONDS,
            )
        try:
            await asyncio.sleep(_RETRY_SECONDS)
        except asyncio.CancelledError:
            raise
