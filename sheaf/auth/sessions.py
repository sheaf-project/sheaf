import secrets
import uuid

import redis.asyncio as redis

from sheaf.config import settings

_redis: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _session_key(session_id: str) -> str:
    return f"sheaf:session:{session_id}"


async def create_session(user_id: uuid.UUID) -> str:
    """Create a new session, return the session ID."""
    r = await get_redis()
    session_id = secrets.token_urlsafe(32)
    await r.setex(
        _session_key(session_id),
        settings.session_expire_hours * 3600,
        str(user_id),
    )
    return session_id


async def get_session_user_id(session_id: str) -> uuid.UUID | None:
    """Look up the user ID for a session. Returns None if expired/invalid."""
    r = await get_redis()
    user_id_str = await r.get(_session_key(session_id))
    if user_id_str is None:
        return None
    return uuid.UUID(user_id_str)


async def delete_session(session_id: str) -> None:
    r = await get_redis()
    await r.delete(_session_key(session_id))
