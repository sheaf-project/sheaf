"""User-opt-in front-history retention sweep.

A front is aged out only when ALL of these hold together (the design's
"Option B" predicate):

  - its owning system opted in (``front_retention_days`` > 0),
  - it is closed (``ended_at`` IS NOT NULL) - open fronts are never pruned,
  - it ended long ago in the real world (``ended_at`` older than that system's
    own ``front_retention_days`` window), and
  - it has lived in this database past the fixed import grace
    (``created_at`` older than ``front_retention_import_grace_days``).

The import-grace clause is the load-bearing one: an imported front carries an
old real-world ``ended_at`` but lands with ``created_at`` = now, so it must not
be aged out until it has actually lived here for the grace. Case (2) below is
the regression guard for that - it is a retention bug that was caught and fixed
the same day, no data lost.

Unlike the retired operator pruner, this sweep is NOT mode-gated (it is a user
privacy control, so it runs in self-hosted too) and is keyed off the
per-system ``front_retention_days`` setting, not the user tier. Fronts are
seeded directly with explicit timestamps - including ``created_at`` - because
the API / import paths only ever stamp "now" for the insert time.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx


def _register(client: httpx.Client) -> str:
    email = f"front-ret-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return email


def _engine_session():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from sheaf.config import settings

    db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
    engine = create_async_engine(db_url)
    return engine, sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _seed(email: str, retention_days: int, specs: list[tuple]) -> list:
    """Set the user's system ``front_retention_days`` and insert one Front per
    spec.

    specs is a list of (created_at, started_at, ended_at|None). ``created_at``
    is set explicitly: it is the row-insertion time the grace clause keys off,
    and the real API / import paths only ever stamp it "now", so a test has to
    backdate it directly. Returns the new front ids in the same order.
    """

    async def _run() -> list:
        from sqlalchemy import select

        from sheaf.crypto import blind_index
        from sheaf.models.front import Front
        from sheaf.models.system import System
        from sheaf.models.user import User

        engine, async_session = _engine_session()
        ids = []
        async with async_session() as db:
            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
            ).scalar_one()
            system = (
                await db.execute(select(System).where(System.user_id == user.id))
            ).scalar_one()
            system.front_retention_days = retention_days
            for created, started, ended in specs:
                front = Front(
                    system_id=system.id,
                    started_at=started,
                    ended_at=ended,
                    created_at=created,
                )
                db.add(front)
                await db.flush()
                ids.append(front.id)
            await db.commit()
        await engine.dispose()
        return ids

    return asyncio.run(_run())


def _surviving_front_ids(email: str) -> set:
    async def _run() -> set:
        from sqlalchemy import select

        from sheaf.crypto import blind_index
        from sheaf.models.front import Front
        from sheaf.models.system import System
        from sheaf.models.user import User

        engine, async_session = _engine_session()
        async with async_session() as db:
            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
            ).scalar_one()
            rows = (
                await db.execute(
                    select(Front.id)
                    .join(System, Front.system_id == System.id)
                    .where(System.user_id == user.id)
                )
            ).all()
        await engine.dispose()
        return {r[0] for r in rows}

    return asyncio.run(_run())


def _retention_traces(email: str) -> list:
    """Return the detail dicts of every RETENTION_PRUNED activity row for the
    user, so the nothing-silent trace can be asserted."""

    async def _run() -> list:
        from sqlalchemy import select

        from sheaf.crypto import blind_index
        from sheaf.models.activity_event import ActivityAction, ActivityEvent
        from sheaf.models.user import User

        engine, async_session = _engine_session()
        async with async_session() as db:
            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
            ).scalar_one()
            rows = (
                await db.execute(
                    select(ActivityEvent).where(
                        ActivityEvent.user_id == user.id,
                        ActivityEvent.action == ActivityAction.RETENTION_PRUNED,
                    )
                )
            ).scalars().all()
        await engine.dispose()
        return [r.detail for r in rows]

    return asyncio.run(_run())


def _run_sweep() -> dict:
    async def _run() -> dict:
        from sheaf.services.front_retention import sweep_front_retention

        engine, async_session = _engine_session()
        async with async_session() as db:
            result = await sweep_front_retention(db)
            await db.commit()
        await engine.dispose()
        return result

    return asyncio.run(_run())


def test_sweep_ages_out_opted_in_old_history(client: httpx.Client, monkeypatch):
    """Pin every arm of the Option B predicate for an opted-in system, plus the
    opted-out escape and the nothing-silent activity trace."""
    from sheaf.config import settings

    # Pin the import grace so the test doesn't ride on the config default.
    monkeypatch.setattr(settings, "front_retention_import_grace_days", 14)

    now = datetime.now(UTC)

    # Opted-in system: 30-day window.
    opted_in = _register(client)
    pruned, imported, recent_end, still_open = _seed(
        opted_in,
        30,
        [
            # (1) Genuinely old native front: inserted ~200d ago, ended ~190d
            # ago. Old on every axis and past the import grace -> PRUNED.
            (
                now - timedelta(days=200),
                now - timedelta(days=200),
                now - timedelta(days=190),
            ),
            # (2) IMPORTED old history: carries a ~2-year-old real-world
            # start/end but landed just now (created_at = now). Ended long ago
            # yet inside the import grace -> MUST SURVIVE. This is the
            # regression guard for the retention bug.
            (now, now - timedelta(days=730), now - timedelta(days=700)),
            # (3) Long native front that only just ended: inserted ~200d ago but
            # ended 5d ago, inside the 30d window -> MUST SURVIVE.
            (
                now - timedelta(days=200),
                now - timedelta(days=200),
                now - timedelta(days=5),
            ),
            # (4) Open front (ended_at NULL): never pruned while ongoing, even
            # though inserted long ago.
            (now - timedelta(days=200), now - timedelta(days=200), None),
        ],
    )

    # Opted-out system (front_retention_days = 0): an equally-old front that
    # WOULD be pruned if it were opted in. Retention off means never touched.
    opted_out = _register(client)
    (untouched,) = _seed(
        opted_out,
        0,
        [
            (
                now - timedelta(days=200),
                now - timedelta(days=200),
                now - timedelta(days=190),
            ),
        ],
    )

    result = _run_sweep()
    # items_processed is a global count across all opted-in systems, so only
    # assert the sweep removed at least our one eligible front; per-user
    # survival is checked against each account below.
    assert result["items_processed"] >= 1

    surviving = _surviving_front_ids(opted_in)
    assert pruned not in surviving, (
        "front ended and inserted long ago on an opted-in system should be aged out"
    )
    assert imported in surviving, (
        "REGRESSION: freshly-imported old history was aged out - the import "
        "grace (created_at) must protect it."
    )
    assert recent_end in surviving, "front that ended inside the window was wrongly pruned"
    assert still_open in surviving, "open front must never be pruned"

    # Opted-out system: nothing touched.
    assert untouched in _surviving_front_ids(opted_out), (
        "a system with front_retention_days = 0 must never have fronts pruned"
    )

    # Nothing-silent: exactly the opted-in user gets a RETENTION_PRUNED trace
    # carrying the count actually removed for them (case 1 only).
    traces = _retention_traces(opted_in)
    assert traces == [{"fronts_pruned": 1}], traces
    assert _retention_traces(opted_out) == [], (
        "opted-out user must get no retention trace"
    )
