import re
import secrets
import uuid
from datetime import UTC, datetime

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


def _user_sessions_key(user_id: uuid.UUID) -> str:
    return f"sheaf:user_sessions:{user_id}"


def _parse_client_name(user_agent: str, client_header: str | None = None) -> str:
    """Extract a friendly client name from headers.

    If X-Sheaf-Client is set (e.g. "Sheaf Android/1.2.0"), use it directly.
    Otherwise parse browser name from User-Agent.
    """
    if client_header:
        return client_header.strip()

    if not user_agent:
        return "Unknown"

    # Order matters — Edge contains "Chrome", Chrome contains "Safari"
    if "Edg/" in user_agent or "Edge/" in user_agent:
        return "Edge"
    if "Firefox/" in user_agent:
        return "Firefox"
    if "OPR/" in user_agent or "Opera/" in user_agent:
        return "Opera"
    if "Chrome/" in user_agent or "Chromium/" in user_agent:
        return "Chrome"
    if "Safari/" in user_agent:
        return "Safari"

    # Generic bot/tool detection
    if re.search(r"(curl|wget|httpx|python|node|go-http)", user_agent, re.IGNORECASE):
        return "HTTP client"

    return "Unknown"


async def create_session(
    user_id: uuid.UUID,
    ip: str | None = None,
    user_agent: str = "",
    client_header: str | None = None,
) -> str:
    """Create a new session with metadata, return the session ID."""
    r = await get_redis()
    session_id = secrets.token_urlsafe(32)
    now = datetime.now(UTC).isoformat()
    client_name = _parse_client_name(user_agent, client_header)

    ttl = settings.session_expire_hours * 3600

    pipe = r.pipeline()
    pipe.hset(
        _session_key(session_id),
        mapping={
            "user_id": str(user_id),
            "created_at": now,
            "created_ip": ip or "",
            "last_active_at": now,
            "last_active_ip": ip or "",
            "user_agent": user_agent,
            "client_name": client_name,
            "nickname": "",
        },
    )
    pipe.expire(_session_key(session_id), ttl)
    pipe.sadd(_user_sessions_key(user_id), session_id)
    await pipe.execute()

    return session_id


async def get_session_user_id(session_id: str) -> uuid.UUID | None:
    """Look up the user ID for a session. Returns None if expired/invalid."""
    r = await get_redis()
    user_id_str = await r.hget(_session_key(session_id), "user_id")
    if user_id_str is None:
        return None
    return uuid.UUID(user_id_str)


async def touch_session(session_id: str, ip: str | None = None) -> None:
    """Update last_active_at, last_active_ip, and extend session TTL."""
    r = await get_redis()
    now = datetime.now(UTC).isoformat()
    ttl = settings.session_expire_hours * 3600
    mapping: dict[str, str] = {"last_active_at": now}
    if ip:
        mapping["last_active_ip"] = ip
    pipe = r.pipeline()
    pipe.hset(_session_key(session_id), mapping=mapping)
    pipe.expire(_session_key(session_id), ttl)
    await pipe.execute()


async def get_session_info(session_id: str) -> dict | None:
    """Return full session metadata as a dict, or None if expired."""
    r = await get_redis()
    data = await r.hgetall(_session_key(session_id))
    if not data:
        return None
    return data


async def list_user_sessions(user_id: uuid.UUID) -> list[dict]:
    """List all active sessions for a user, cleaning up expired entries."""
    r = await get_redis()
    set_key = _user_sessions_key(user_id)
    session_ids = await r.smembers(set_key)

    sessions = []
    expired = []

    for sid in session_ids:
        data = await r.hgetall(_session_key(sid))
        if not data:
            expired.append(sid)
            continue
        sessions.append({"id": sid, **data})

    # Clean up expired session references
    if expired:
        await r.srem(set_key, *expired)

    return sessions


async def delete_session(session_id: str) -> None:
    """Delete a session and remove it from the user's session set."""
    r = await get_redis()
    # Get user_id before deleting so we can clean the set
    user_id_str = await r.hget(_session_key(session_id), "user_id")
    await r.delete(_session_key(session_id))
    if user_id_str:
        await r.srem(_user_sessions_key(uuid.UUID(user_id_str)), session_id)


async def delete_other_sessions(
    user_id: uuid.UUID, keep_session_id: str,
) -> int:
    """Revoke all sessions for a user except the given one. Returns count revoked."""
    r = await get_redis()
    set_key = _user_sessions_key(user_id)
    session_ids = await r.smembers(set_key)

    revoked = 0
    for sid in session_ids:
        if sid == keep_session_id:
            continue
        await r.delete(_session_key(sid))
        await r.srem(set_key, sid)
        revoked += 1

    return revoked


async def delete_all_user_sessions(user_id: uuid.UUID) -> int:
    """Delete all sessions for a user. Returns count deleted."""
    r = await get_redis()
    set_key = _user_sessions_key(user_id)
    session_ids = await r.smembers(set_key)

    if not session_ids:
        return 0

    pipe = r.pipeline()
    for sid in session_ids:
        pipe.delete(_session_key(sid))
    pipe.delete(set_key)
    await pipe.execute()

    return len(session_ids)


async def rename_session(session_id: str, nickname: str) -> bool:
    """Set a nickname on a session. Returns False if session doesn't exist."""
    r = await get_redis()
    if not await r.exists(_session_key(session_id)):
        return False
    await r.hset(_session_key(session_id), "nickname", nickname)
    return True


# ---------------------------------------------------------------------------
# Admin step-up auth (per-user, auth-method-agnostic)
# ---------------------------------------------------------------------------

def _step_up_key(user_id: uuid.UUID) -> str:
    return f"sheaf:admin_step_up:{user_id}"


async def set_admin_step_up(user_id: uuid.UUID, ttl: int = 7200) -> None:
    """Mark a user as having completed admin step-up auth. TTL default: 2 hours."""
    r = await get_redis()
    await r.setex(_step_up_key(user_id), ttl, "1")


async def check_admin_step_up(user_id: uuid.UUID) -> bool:
    """Return True if the user has a valid admin step-up token."""
    r = await get_redis()
    return await r.exists(_step_up_key(user_id)) == 1
