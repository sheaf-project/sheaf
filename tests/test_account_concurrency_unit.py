"""The per-account in-flight cap, exercised without a server.

`AccountSlots` is the whole mechanism: a semaphore per account that forgets
itself once idle. These pin the properties the incident analysis relies on -
a burst is served in batches of `limit` rather than refused, a request that
waits too long gets a clean timeout, the registry does not leak an entry per
account that ever made a request, and the release handle is safe to call
twice (it is wired into a middleware `finally`).
"""

from __future__ import annotations

import asyncio

import pytest

from sheaf.middleware.concurrency import AccountSlots


async def _hold(slots: AccountSlots, key: str, *, limit: int, gate: asyncio.Event):
    release, waited = await slots.acquire(key, limit, timeout=5)
    try:
        await gate.wait()
    finally:
        release()
    return waited


async def test_serves_a_burst_in_batches_rather_than_refusing():
    """Twenty requests against a cap of three: at most three run at once,
    every one of them completes, and the ones past the first three report
    having waited."""
    slots = AccountSlots()
    limit = 3
    running = 0
    peak = 0
    waited_flags: list[bool] = []

    async def one():
        nonlocal running, peak
        release, waited = await slots.acquire("acct", limit, timeout=5)
        waited_flags.append(waited)
        running += 1
        peak = max(peak, running)
        try:
            await asyncio.sleep(0.01)
        finally:
            running -= 1
            release()

    await asyncio.gather(*(one() for _ in range(20)))
    assert peak == limit
    assert len(waited_flags) == 20
    assert waited_flags[:limit] == [False] * limit
    assert all(waited_flags[limit:])


async def test_times_out_when_no_slot_frees_up():
    slots = AccountSlots()
    gate = asyncio.Event()
    holder = asyncio.create_task(_hold(slots, "acct", limit=1, gate=gate))
    await asyncio.sleep(0)  # let the holder take the only slot
    with pytest.raises(TimeoutError):
        await slots.acquire("acct", 1, timeout=0.05)
    gate.set()
    await holder
    # The timed-out waiter withdrew its interest: nothing is left behind.
    assert len(slots) == 0


async def test_accounts_do_not_share_a_cap():
    """One account at its cap must not delay another. This is the property
    that turns 'one client slows everyone' into 'one client slows itself'."""
    slots = AccountSlots()
    gate = asyncio.Event()
    holders = [
        asyncio.create_task(_hold(slots, "busy", limit=2, gate=gate))
        for _ in range(2)
    ]
    await asyncio.sleep(0)
    release, waited = await asyncio.wait_for(
        slots.acquire("quiet", 2, timeout=0.05), timeout=1
    )
    assert waited is False
    release()
    gate.set()
    await asyncio.gather(*holders)


async def test_registry_forgets_idle_accounts():
    """An entry per account that ever made a request would be a slow leak on
    a long-lived process. Once holders and waiters are both gone, the key is
    dropped, and the next request for it starts fresh."""
    slots = AccountSlots()
    release, _ = await slots.acquire("acct", 4, timeout=1)
    assert len(slots) == 1
    release()
    assert len(slots) == 0


async def test_release_is_idempotent():
    """The release handle sits in a middleware `finally`; a double call must
    not over-release the semaphore and quietly raise the cap."""
    slots = AccountSlots()
    release, _ = await slots.acquire("acct", 1, timeout=1)
    release()
    release()
    # Cap still 1: a holder blocks the next acquire.
    gate = asyncio.Event()
    holder = asyncio.create_task(_hold(slots, "acct", limit=1, gate=gate))
    await asyncio.sleep(0)
    with pytest.raises(TimeoutError):
        await slots.acquire("acct", 1, timeout=0.05)
    gate.set()
    await holder


async def test_waiter_survives_the_registry_dropping_its_key():
    """A waiter woken by a release must still be able to release into the
    same semaphore it acquired from, even if nobody else is interested in
    the key any more by then. Interest is counted before the wait starts,
    so the entry cannot be dropped underneath a waiter."""
    slots = AccountSlots()
    gate = asyncio.Event()
    holder = asyncio.create_task(_hold(slots, "acct", limit=1, gate=gate))
    await asyncio.sleep(0)

    async def waiter():
        release, waited = await slots.acquire("acct", 1, timeout=5)
        assert waited is True
        release()

    w = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    assert len(slots) == 1
    gate.set()
    await asyncio.gather(holder, w)
    assert len(slots) == 0
