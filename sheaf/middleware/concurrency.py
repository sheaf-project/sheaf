"""Per-account request concurrency cap.

What made the API slow on 2026-09-22 was not request *rate* but request
*concurrency*: a client fired 76 requests in a single second, every one of
them checked out a pool connection, and everybody else's requests then
queued behind that one account for a connection. Median latency went to
around half a second, several seconds at the tail, mostly noticeable on the
screens that do the fan-out; internal alerting caught it, users mostly did
not. A per-minute counter cannot see that shape - the burst is over before a
window closes - and sized to catch it, it would refuse a large system's
ordinary page load.

So this bounds in-flight requests per account instead. Each account gets an
`asyncio.Semaphore(limit)`; a request that finds it full WAITS (holding no
pool connection) rather than failing, for up to a bounded time, and only then
gets a 429 with `Retry-After`. A client that fans out 200 requests at once
sees them served in orderly batches of `limit` rather than a wall of errors,
and other accounts keep their share of the pool throughout. The current
mobile apps, which do exactly that fan-out, keep working; they just stop
being able to slow anyone else down.

Process-local on purpose. There is no Redis round trip on the hot path and
no distributed state to get wrong; with `WEB_CONCURRENCY=n` the worst case
is `n * limit` in flight for one account, which is still a bound. The slot is
taken inside `get_current_user`, so every authenticated route is covered
without any router having to remember a dependency, and it is released by
`AccountConcurrencyMiddleware` once the response is on its way.

"On its way" is deliberate: `BaseHTTPMiddleware.call_next` returns as soon as
the response headers are ready, so a streaming response (the SSE front
stream, an export download) gives its slot back at that point and holds it
only for the part that touched the database. A stream open for an hour costs
one slot for a few milliseconds, not for the hour. That is the exemption the
long-lived routes need, and it falls out of the mechanism rather than being a
list of paths to keep in sync.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware

from sheaf.config import settings
from sheaf.observability.metrics import (
    account_concurrency_total,
    account_concurrency_wait_seconds,
    rate_limit_checks_total,
)

logger = logging.getLogger("sheaf.concurrency")

_STATE_KEY = "account_slot_release"
_LIMIT_DETAIL = (
    "Too many requests in flight for this account. Wait for some to finish "
    "and try again."
)
# Metric bucket label, so a slot timeout shows up on the same 429 panel as
# every other blocked check (`sheaf_rate_limit_checks_total{outcome="blocked"}`).
_BUCKET = "concurrency"


class _Slot:
    """One account's semaphore plus a count of everyone who cares about it.

    `interest` is holders + waiters. It is incremented BEFORE the acquire
    awaits and decremented AFTER the release, so an entry is only ever dropped
    from the registry when nobody holds it and nobody is waiting on it. A
    waiter woken by a release therefore always finds its own `_Slot` still
    registered, and a fresh request for the same account after the drop gets a
    fresh semaphore rather than sharing one that is about to be forgotten.
    """

    __slots__ = ("interest", "sem")

    def __init__(self, limit: int) -> None:
        self.sem = asyncio.Semaphore(limit)
        self.interest = 0


class AccountSlots:
    """Registry of per-key semaphores that forgets a key once it is idle."""

    def __init__(self) -> None:
        self._slots: dict[str, _Slot] = {}

    def __len__(self) -> int:
        return len(self._slots)

    async def acquire(
        self, key: str, limit: int, timeout: float
    ) -> tuple[Callable[[], None], bool]:
        """Take a slot for `key`, waiting up to `timeout` seconds.

        Returns `(release, waited)`. `release` is idempotent, so a caller can
        wire it into a `finally` without worrying about double calls. `waited`
        is True when the slot was full on arrival, which is the signal worth
        counting: it means this account is at its cap right now.

        Raises `TimeoutError` if no slot came free in time. The cancelled
        acquire is handed back to the semaphore correctly (asyncio re-wakes
        the next waiter if the cancellation raced a release).
        """
        slot = self._slots.get(key)
        if slot is None:
            slot = _Slot(limit)
            self._slots[key] = slot
        slot.interest += 1
        waited = slot.sem.locked()
        try:
            await asyncio.wait_for(slot.sem.acquire(), timeout)
        except BaseException:
            self._drop_interest(key, slot)
            raise

        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            slot.sem.release()
            self._drop_interest(key, slot)

        return release, waited

    def _drop_interest(self, key: str, slot: _Slot) -> None:
        slot.interest -= 1
        if slot.interest <= 0 and self._slots.get(key) is slot:
            del self._slots[key]


_slots = AccountSlots()


async def acquire_account_slot(request: Request, user_id) -> None:
    """Take this request's slot for `user_id`, or 429 after the wait.

    Called from `get_current_user` once the account is known. A second call
    on the same request (the auth chain can run twice on a few routes) is a
    no-op: one request, one slot. `0` (or less) for the limit switches the
    whole mechanism off.
    """
    limit = settings.account_concurrency_limit
    if limit <= 0:
        return
    if getattr(request.state, _STATE_KEY, None) is not None:
        return

    started = time.perf_counter()
    try:
        release, waited = await _slots.acquire(
            str(user_id), limit, settings.account_concurrency_wait_seconds
        )
    except TimeoutError:
        elapsed = time.perf_counter() - started
        account_concurrency_wait_seconds.observe(elapsed)
        account_concurrency_total.labels(outcome="timed_out").inc()
        rate_limit_checks_total.labels(
            bucket=_BUCKET, scope="per_user", outcome="blocked"
        ).inc()
        logger.info(
            "concurrency: account at %d in-flight for %.1fs, refusing "
            "(method=%s)",
            limit,
            elapsed,
            request.method,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_LIMIT_DETAIL,
            headers={"Retry-After": "1"},
        ) from None

    account_concurrency_wait_seconds.observe(time.perf_counter() - started)
    account_concurrency_total.labels(
        outcome="waited" if waited else "immediate"
    ).inc()
    rate_limit_checks_total.labels(
        bucket=_BUCKET, scope="per_user", outcome="allowed"
    ).inc()
    setattr(request.state, _STATE_KEY, release)


def release_account_slot(request: Request) -> None:
    """Give back whatever `acquire_account_slot` took. Safe to call always."""
    release = getattr(request.state, _STATE_KEY, None)
    if release is not None:
        release()


class AccountConcurrencyMiddleware(BaseHTTPMiddleware):
    """Releases the request's account slot once the response is on its way.

    The acquire lives in the auth dependency (it needs the account); the
    release has to live somewhere that runs whatever the handler did, and a
    middleware `finally` is that place. See the module docstring for why
    "once the response is on its way" is the right moment for streams.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        finally:
            release_account_slot(request)
