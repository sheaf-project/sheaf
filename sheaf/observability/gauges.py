"""Background gauge updater.

Some metrics are DB-sourced (COUNT(*) FROM users WHERE ...) or Redis-sourced
(active session keys, per-IP rate counters). Querying these on every
scrape would put scrape latency on the critical path of every Prometheus
poll — and the values don't change fast enough to justify that. Instead a
single async job runs every `metrics_gauge_refresh_seconds` and sets the
gauges; scrape reads cached values.

Registered as `refresh_metrics_gauges` via `register_job` from
`sheaf/services/jobs.py`. The job dispatcher calls `refresh_gauges(db)`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.observability.metrics import (
    auth_lockouts_active,
    auth_sessions_active,
    auth_totp_enabled,
    auth_trusted_devices_active,
    db_pool_connections,
    imports_in_progress,
    members_custom_front,
    members_total,
    notifications_outbox_depth,
    notifications_outbox_oldest_pending_seconds,
    notifications_subscriptions_active,
    pending_actions_active,
    redis_up,
    requests_per_account_per_minute,
    requests_per_ip_per_minute,
    systems_total,
    users_pending_delete,
    users_total,
)

logger = logging.getLogger("sheaf.metrics.gauges")

# Bounded SCAN: skip the histogram pass entirely on a deployment with
# more keys than this. The intent is "see the busy tail"; if we'd be
# walking 100k+ keys per refresh, the cost outweighs the value and the
# operator should switch to redis-exporter-side metrics instead.
_MAX_RATE_LIMIT_KEYS_PER_REFRESH = 50_000


async def refresh_gauges(db: AsyncSession) -> dict:
    """One refresh pass. Returns the jobs.py-style result dict."""
    # DB-sourced counts. Each query is a single COUNT(*) against an
    # indexed predicate (locked_until, expires_at, deliver_after).
    await _refresh_db_counts(db)
    await _refresh_outbox(db)
    await _refresh_pending_actions(db)
    await _refresh_imports_in_progress(db)
    await _refresh_subscriptions(db)

    # Redis-sourced. Each is a SCAN-and-count or a single value read.
    await _refresh_redis_gauges()

    # Engine pool stats. Free to read; doesn't touch the DB.
    _refresh_db_pool()

    return {"items_processed": 1}


async def _refresh_db_counts(db: AsyncSession) -> None:
    from sheaf.models.member import Member
    from sheaf.models.system import System
    from sheaf.models.trusted_device import TrustedDevice
    from sheaf.models.user import AccountStatus, User

    now = datetime.now(UTC)

    total_users = await db.scalar(select(func.count(User.id)))
    users_total.set(int(total_users or 0))

    pending = await db.scalar(
        select(func.count(User.id)).where(
            User.account_status == AccountStatus.PENDING_DELETION
        )
    )
    users_pending_delete.set(int(pending or 0))

    locked = await db.scalar(
        select(func.count(User.id)).where(User.locked_until > now)
    )
    auth_lockouts_active.set(int(locked or 0))

    totp_on = await db.scalar(
        select(func.count(User.id)).where(User.totp_enabled.is_(True))
    )
    auth_totp_enabled.set(int(totp_on or 0))

    trusted = await db.scalar(
        select(func.count(TrustedDevice.id)).where(TrustedDevice.expires_at > now)
    )
    auth_trusted_devices_active.set(int(trusted or 0))

    sys_count = await db.scalar(select(func.count(System.id)))
    systems_total.set(int(sys_count or 0))

    mem_count = await db.scalar(select(func.count(Member.id)))
    members_total.set(int(mem_count or 0))

    custom_fronts = await db.scalar(
        select(func.count(Member.id)).where(Member.is_custom_front.is_(True))
    )
    members_custom_front.set(int(custom_fronts or 0))


async def _refresh_outbox(db: AsyncSession) -> None:
    from sheaf.models.notification_outbox import NotificationOutboxRow

    pending = await db.scalar(
        select(func.count(NotificationOutboxRow.id)).where(
            NotificationOutboxRow.delivered_at.is_(None)
        )
    )
    notifications_outbox_depth.set(int(pending or 0))

    oldest = await db.scalar(
        select(func.min(NotificationOutboxRow.deliver_after)).where(
            NotificationOutboxRow.delivered_at.is_(None)
        )
    )
    if oldest is None:
        notifications_outbox_oldest_pending_seconds.set(0)
    else:
        age = (datetime.now(UTC) - oldest).total_seconds()
        notifications_outbox_oldest_pending_seconds.set(max(age, 0))


async def _refresh_pending_actions(db: AsyncSession) -> None:
    from sheaf.models.pending_action import PendingAction, PendingActionStatus

    result = await db.execute(
        select(PendingAction.action_type, func.count(PendingAction.id))
        .where(PendingAction.status == PendingActionStatus.PENDING)
        .group_by(PendingAction.action_type)
    )
    by_type = dict(result.all())

    # We don't know the full category set without enumerating
    # PendingActionType, but absent-then-present is OK: the gauge starts
    # at zero on first observation and rises as rows appear. Reset
    # categories we previously had to zero by zeroing the entire space
    # first.
    from sheaf.models.pending_action import PendingActionType

    for cat in PendingActionType:
        pending_actions_active.labels(category=cat.value).set(
            int(by_type.get(cat.value, 0))
        )


async def _refresh_imports_in_progress(db: AsyncSession) -> None:
    from sheaf.models.import_job import ImportJob, ImportJobStatus

    n = await db.scalar(
        select(func.count(ImportJob.id)).where(
            ImportJob.status.in_(
                [ImportJobStatus.PENDING.value, ImportJobStatus.RUNNING.value]
            )
        )
    )
    imports_in_progress.set(int(n or 0))


async def _refresh_subscriptions(db: AsyncSession) -> None:
    from sheaf.models.notification_channel import (
        DestinationState,
        NotificationChannel,
    )

    result = await db.execute(
        select(
            NotificationChannel.destination_type, func.count(NotificationChannel.id),
        )
        .where(NotificationChannel.destination_state == DestinationState.ACTIVE.value)
        .group_by(NotificationChannel.destination_type)
    )
    by_type = dict(result.all())
    for channel_type in (
        "web_push", "mobile_push", "webhook", "ntfy", "pushover", "discord", "email",
    ):
        notifications_subscriptions_active.labels(channel_type=channel_type).set(
            int(by_type.get(channel_type, 0))
        )


async def _refresh_redis_gauges() -> None:
    try:
        from sheaf.auth.sessions import get_redis

        r = await get_redis()
    except Exception:
        redis_up.set(0)
        return

    try:
        await r.ping()
    except Exception:
        redis_up.set(0)
        return
    redis_up.set(1)

    # Active sessions. SCAN with COUNT hint; bail if it gets long.
    sessions = 0
    async for _ in _scan(r, "sheaf:session:*", _MAX_RATE_LIMIT_KEYS_PER_REFRESH):
        sessions += 1
    auth_sessions_active.set(sessions)

    # Per-IP request distribution from the global rate-limit counters.
    # Each key is one IP for one window; the value is request count in
    # that window (window = settings.rate_limit_global_window seconds).
    # We observe the per-minute-equivalent into the histogram.
    from sheaf.config import settings as _settings

    window = max(_settings.rate_limit_global_window, 1)
    per_minute_factor = 60.0 / window

    n = 0
    async for key in _scan(r, "sheaf:rl:ip:*:global:*", _MAX_RATE_LIMIT_KEYS_PER_REFRESH):
        try:
            val = await r.get(key)
            if val is None:
                continue
            requests_per_ip_per_minute.observe(int(val) * per_minute_factor)
        except Exception:
            continue
        n += 1
        if n >= _MAX_RATE_LIMIT_KEYS_PER_REFRESH:
            logger.warning(
                "metrics: per-IP histogram pass hit %d-key cap; reading "
                "incomplete distribution. Consider redis-exporter for "
                "deployments at this scale.", _MAX_RATE_LIMIT_KEYS_PER_REFRESH,
            )
            break

    # Per-account request distribution from user-scoped per-endpoint counters.
    n = 0
    async for key in _scan(r, "sheaf:rl:user:*", _MAX_RATE_LIMIT_KEYS_PER_REFRESH):
        try:
            val = await r.get(key)
            if val is None:
                continue
            requests_per_account_per_minute.observe(int(val) * per_minute_factor)
        except Exception:
            continue
        n += 1
        if n >= _MAX_RATE_LIMIT_KEYS_PER_REFRESH:
            break


async def _scan(r, pattern: str, cap: int) -> AsyncIterator[str]:
    """Async generator over SCAN matches, bounded by `cap`."""
    cursor: int | str = 0
    seen = 0
    while True:
        cursor, keys = await r.scan(cursor=cursor, match=pattern, count=500)
        for k in keys:
            yield k.decode() if isinstance(k, bytes) else k
            seen += 1
            if seen >= cap:
                return
        if cursor in (0, "0"):
            return


def _refresh_db_pool() -> None:
    from sheaf.database import engine

    try:
        pool = engine.pool
        db_pool_connections.labels(state="checked_in").set(pool.checkedin())
        db_pool_connections.labels(state="checked_out").set(pool.checkedout())
    except Exception:
        # Some pool classes (NullPool) don't expose these; the gauge
        # just stays at its last value.
        pass
