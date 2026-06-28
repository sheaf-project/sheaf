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
    FRONT_COUNT_BUCKETS,
    auth_lockouts_active,
    auth_sessions_active,
    auth_totp_enabled,
    auth_trusted_devices_active,
    cf_shield_active,
    content_revisions_total,
    db_pool_connections,
    fronts_total,
    imports_in_progress,
    imports_oldest_pending_seconds,
    journal_entries_total,
    members_custom_front,
    members_total,
    notifications_outbox_depth,
    notifications_outbox_oldest_pending_seconds,
    notifications_subscriptions_active,
    pending_actions_active,
    redis_up,
    requests_per_account_per_minute,
    requests_per_ip_per_minute,
    system_front_count_max,
    system_journal_entry_count_max,
    systems_by_front_count,
    systems_by_journal_entry_count,
    systems_total,
    target_revision_count_max,
    targets_by_revision_count,
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
    """Slow-gauges pass. Runs on the job-runner cadence, so detection
    lag is bounded by job_check_interval_minutes. Use this for counts
    that don't move on a per-second timescale (user/system counts,
    subscriptions, pending actions, full rate-limit histogram sampling).

    Fast-moving signals (redis_up, db_pool, outbox depth) ride
    `refresh_fast_gauges()` on the dedicated lifespan task instead.
    """
    # DB-sourced counts. Each query is a single COUNT(*) against an
    # indexed predicate (locked_until, expires_at, deliver_after).
    await _refresh_db_counts(db)
    await _refresh_outbox(db)
    await _refresh_pending_actions(db)
    await _refresh_imports_in_progress(db)
    await _refresh_subscriptions(db)

    # Per-IP / per-account rate-distribution sampling — slow because it
    # walks every rate-limit counter in Redis.
    await _refresh_rate_distribution()
    await _refresh_shield_state()

    return {"items_processed": 1}


async def refresh_fast_gauges() -> None:
    """Fast-gauges pass. Runs on the dedicated `fast_gauges_loop` task
    on a short (~10s) cadence so up/down detection isn't bounded by the
    job-runner's coarse wake interval. Keeps the work cheap: a Redis
    PING, the pool's checked-in/out counters, one outbox COUNT(*).
    """
    await _refresh_redis_up_only()
    _refresh_db_pool()
    await _refresh_outbox_depth_only()


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

    from sheaf.models.front import Front

    total_fronts = await db.scalar(select(func.count(Front.id)))
    fronts_total.set(int(total_fronts or 0))

    # Per-system front-count distribution. One grouped query; the outer join
    # keeps systems with zero fronts in the picture so the distribution
    # covers every system. We never label by system_id - we set a snapshot
    # cumulative gauge (count of systems at/under each threshold) plus the
    # single max, which is enough to spot an outlier without naming it.
    per_system = (
        (
            await db.execute(
                select(func.count(Front.id))
                .select_from(System)
                .outerjoin(Front, Front.system_id == System.id)
                .group_by(System.id)
            )
        )
        .scalars()
        .all()
    )
    counts = [int(c or 0) for c in per_system]
    system_front_count_max.set(max(counts) if counts else 0)
    for threshold in FRONT_COUNT_BUCKETS:
        systems_by_front_count.labels(le=str(threshold)).set(
            sum(1 for c in counts if c <= threshold)
        )
    systems_by_front_count.labels(le="+Inf").set(len(counts))

    # Journal entries + content-revision (edit-history) volume, same
    # preserve-by-count lens. One grouped query each; the snapshot CDF +
    # max are set the same way as the front distribution above.
    from sheaf.models.content_revision import ContentRevision
    from sheaf.models.journal_entry import JournalEntry

    def _set_distribution(cdf_gauge, max_gauge, values: list[int]) -> None:
        max_gauge.set(max(values) if values else 0)
        for threshold in FRONT_COUNT_BUCKETS:
            cdf_gauge.labels(le=str(threshold)).set(
                sum(1 for v in values if v <= threshold)
            )
        cdf_gauge.labels(le="+Inf").set(len(values))

    je_total = await db.scalar(select(func.count(JournalEntry.id)))
    journal_entries_total.set(int(je_total or 0))
    je_per_system = [
        int(c or 0)
        for c in (
            await db.execute(
                select(func.count(JournalEntry.id))
                .select_from(System)
                .outerjoin(JournalEntry, JournalEntry.system_id == System.id)
                .group_by(System.id)
            )
        )
        .scalars()
        .all()
    ]
    _set_distribution(
        systems_by_journal_entry_count,
        system_journal_entry_count_max,
        je_per_system,
    )

    cr_total = await db.scalar(select(func.count(ContentRevision.id)))
    content_revisions_total.set(int(cr_total or 0))
    # Revisions per target (one journal entry / member bio / message).
    rev_per_target = [
        int(c or 0)
        for c in (
            await db.execute(
                select(func.count(ContentRevision.id)).group_by(
                    ContentRevision.target_type, ContentRevision.target_id
                )
            )
        )
        .scalars()
        .all()
    ]
    _set_distribution(
        targets_by_revision_count, target_revision_count_max, rev_per_target
    )


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

    # Oldest still-pending import: age in seconds. The runner is
    # NOTIFY-driven, so anything beyond a few seconds means it isn't
    # draining. created_at is enqueue time, and the row stays pending
    # until a worker claims it, so this is the time-to-pickup.
    oldest = await db.scalar(
        select(func.min(ImportJob.created_at)).where(
            ImportJob.status == ImportJobStatus.PENDING.value
        )
    )
    if oldest is None:
        imports_oldest_pending_seconds.set(0)
    else:
        age = (datetime.now(UTC) - oldest).total_seconds()
        imports_oldest_pending_seconds.set(max(age, 0))


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


async def _refresh_redis_up_only() -> None:
    """Just the PING. Used by the fast-gauges loop."""
    try:
        from sheaf.auth.sessions import get_redis

        r = await get_redis()
        await r.ping()
    except Exception:
        redis_up.set(0)
        return
    redis_up.set(1)


async def _refresh_outbox_depth_only() -> None:
    """Outbox depth without the oldest-pending walk. Used by the
    fast-gauges loop; the full _refresh_outbox is on the slow path."""
    from sheaf.database import async_session_factory
    from sheaf.models.notification_outbox import NotificationOutboxRow

    async with async_session_factory() as db:
        try:
            pending = await db.scalar(
                select(func.count(NotificationOutboxRow.id)).where(
                    NotificationOutboxRow.delivered_at.is_(None)
                )
            )
        except Exception:
            return
    notifications_outbox_depth.set(int(pending or 0))


async def _refresh_rate_distribution() -> None:
    try:
        from sheaf.auth.sessions import get_redis

        r = await get_redis()
        await r.ping()
    except Exception:
        return

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


async def _refresh_shield_state() -> None:
    """Pull current shield_mode state from Redis into the gauge.

    apply_transition() sets the gauge on every edge, but a backend
    restarting mid-incident wouldn't know until the next transition.
    The refresher closes that gap within one cycle.
    """
    from sheaf.services.shield_mode import get_state

    try:
        state = await get_state()
    except Exception:
        return
    cf_shield_active.set(1 if state.active else 0)


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
