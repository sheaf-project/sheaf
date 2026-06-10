import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from sheaf.config import settings

_ph = PasswordHasher()

# Argon2id is deliberately expensive (~50-150ms CPU and ~64MiB RAM per
# hash at default params). Running it inline on the event loop froze the
# whole single-worker instance for the duration of every login/register/
# step-up check, so the work is pushed to a thread (argon2-cffi releases
# the GIL). Concurrency is bounded the same way image normalization is:
# unbounded to_thread under a credential-stuffing burst would just swap
# the loop stall for an OOM. Excess callers queue on the semaphore, and
# the per-IP rate limits on the auth endpoints keep that queue bounded.
# The settings value is read once on first use; restart to change.
_hash_semaphore: asyncio.Semaphore | None = None


def _get_hash_semaphore() -> asyncio.Semaphore:
    global _hash_semaphore
    if _hash_semaphore is None:
        _hash_semaphore = asyncio.Semaphore(
            max(1, settings.password_hash_concurrency)
        )
    return _hash_semaphore


def _hash_sync(password: str) -> str:
    return _ph.hash(password)


def _verify_sync(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except VerifyMismatchError:
        return False


async def hash_password(password: str) -> str:
    async with _get_hash_semaphore():
        return await asyncio.to_thread(_hash_sync, password)


async def verify_password(plain: str, hashed: str) -> bool:
    async with _get_hash_semaphore():
        return await asyncio.to_thread(_verify_sync, plain, hashed)


# A real Argon2 hash to verify against when there is no user to verify.
# Computed once at import so the login "user not found" branch can spend
# the same CPU a real wrong-password verify would, closing the timing
# oracle that let account existence be probed by response latency. The
# value behind it is irrelevant; only the work matters.
_DUMMY_HASH = _ph.hash("sheaf-login-timing-equaliser")


async def dummy_verify() -> None:
    """Spend one verify's worth of Argon2 CPU and discard the result.

    Call on the unknown-user login branch so its latency matches the
    real-user wrong-password branch. Goes through the same semaphore and
    threadpool as a real verify so the cost and contention behaviour
    line up.
    """
    async with _get_hash_semaphore():
        await asyncio.to_thread(_verify_sync, "x", _DUMMY_HASH)


def needs_rehash(hashed: str) -> bool:
    """Check if a hash needs rehashing due to parameter changes.

    Pure string parsing - cheap enough to stay synchronous.
    """
    return _ph.check_needs_rehash(hashed)
