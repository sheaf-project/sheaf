"""Write and query helpers for the security-event log.

Writes go through `record_security_event`, which uses its OWN database
session (not the request's). That is deliberate: the event is an audit
side-effect, so it must never roll back with a failed auth transaction
(a wrong-password login that we WANT recorded) nor be able to break the
request if the insert fails. Same best-effort contract as the rate-limit
hit history - wrapped so a DB hiccup can't turn a clean 401 into a 500.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.database import async_session_factory
from sheaf.models.security_event import SecurityEvent, SecurityEventType

logger = logging.getLogger("sheaf.security_events")


async def record_security_event(
    *,
    event_type: SecurityEventType,
    outcome: str,
    user_id: uuid.UUID | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    detail: dict | None = None,
) -> None:
    """Append one security event. Best-effort, isolated, never raises.

    Runs on a dedicated short-lived session so it is decoupled from the
    caller's transaction - a login-failure path has already raised by
    the time this matters, and we still want the row.
    """
    try:
        async with async_session_factory() as session:
            session.add(
                SecurityEvent(
                    id=uuid.uuid4(),
                    created_at=datetime.now(UTC),
                    event_type=event_type,
                    outcome=outcome,
                    user_id=user_id,
                    ip=ip,
                    user_agent=(user_agent or None),
                    detail=detail,
                )
            )
            await session.commit()
    except Exception:
        logger.warning(
            "security event write failed (%s/%s)",
            event_type,
            outcome,
            exc_info=True,
        )


def _is_cidr(value: str) -> bool:
    return "/" in value


async def events_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int = 100,
) -> list[SecurityEvent]:
    """One account's security events, newest first."""
    result = await db.execute(
        select(SecurityEvent)
        .where(SecurityEvent.user_id == user_id)
        .order_by(SecurityEvent.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def events_for_ip(
    db: AsyncSession,
    ip_or_cidr: str,
    *,
    limit: int = 200,
) -> list[SecurityEvent]:
    """Security events from an exact IP or a CIDR subnet, newest first.

    `203.0.113.7` matches that address; `203.0.113.0/24` matches the
    whole block via the Postgres `<<=` (contained-or-equal) operator.
    """
    if _is_cidr(ip_or_cidr):
        condition = SecurityEvent.ip.op("<<=")(ip_or_cidr)
    else:
        condition = SecurityEvent.ip == ip_or_cidr
    result = await db.execute(
        select(SecurityEvent)
        .where(condition)
        .order_by(SecurityEvent.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def accounts_seen_from_ip(
    db: AsyncSession,
    ip_or_cidr: str,
) -> list[uuid.UUID]:
    """Distinct known accounts that appear in events from this IP/subnet.

    The ban-evasion / shared-IP signal: which accounts have authenticated
    (or tried to) from here. NULL user_ids (unknown-user attempts) are
    excluded by the DISTINCT.
    """
    if _is_cidr(ip_or_cidr):
        condition = SecurityEvent.ip.op("<<=")(ip_or_cidr)
    else:
        condition = SecurityEvent.ip == ip_or_cidr
    result = await db.execute(
        select(SecurityEvent.user_id)
        .where(condition, SecurityEvent.user_id.is_not(None))
        .distinct()
    )
    return list(result.scalars().all())


async def stuffing_offenders(
    db: AsyncSession,
    *,
    since: datetime,
    min_failures: int = 10,
    limit: int = 50,
) -> list[dict]:
    """Top IPs by failed-login volume since `since`.

    The credential-stuffing fingerprint is one IP failing against MANY
    distinct accounts, so results are ordered by distinct-accounts first,
    then raw failure count (which also surfaces single-account brute force
    and unknown-user scanning, where distinct-accounts stays low).
    """
    distinct_accounts = func.count(func.distinct(SecurityEvent.user_id))
    failures = func.count()
    result = await db.execute(
        select(
            SecurityEvent.ip,
            distinct_accounts.label("distinct_accounts"),
            failures.label("failures"),
            func.max(SecurityEvent.created_at).label("last_seen"),
        )
        .where(
            SecurityEvent.event_type == SecurityEventType.LOGIN,
            SecurityEvent.outcome != "success",
            SecurityEvent.created_at >= since,
            SecurityEvent.ip.is_not(None),
        )
        .group_by(SecurityEvent.ip)
        .having(failures >= min_failures)
        .order_by(distinct_accounts.desc(), failures.desc())
        .limit(limit)
    )
    return [
        {
            "ip": str(row.ip),
            "distinct_accounts": row.distinct_accounts,
            "failures": row.failures,
            "last_seen": row.last_seen,
        }
        for row in result.all()
    ]
