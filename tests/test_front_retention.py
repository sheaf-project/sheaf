"""Front-history retention pruning (aaS free tier).

A front is pruned only when ALL of these hold: it was *inserted* long ago
(``created_at`` < cutoff), it is closed (``ended_at`` IS NOT NULL), and it
also ended long ago (``ended_at`` < cutoff). These tests pin that rule:

  - ``created_at`` (row-insertion time) guards freshly-imported history. This
    is the 2026-06-23 incident: an import carries an old real-world
    ``started_at`` / ``ended_at`` but lands with ``created_at`` = now, so it
    must never be pruned just for being historically old. Keying off
    ``started_at`` (original bug) OR ``ended_at`` alone (the interim fix) both
    delete just-imported history, because imports carry old values for both.
  - ``ended_at`` < cutoff guards a genuinely long-running native front that
    was created long ago but only just closed - its most recent activity is
    recent, so it survives the window after ending.
  - open fronts (``ended_at`` IS NULL) are never pruned while ongoing.

The prune is mode-gated (aaS only), so the service is driven in-process with
sheaf_mode monkeypatched to SAAS and the user forced to the free tier in the
DB. Fronts are seeded directly with explicit timestamps - including
``created_at`` - because the API / import paths only ever stamp "now" for the
insert time.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest


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


def _set_tier_free_and_seed(email: str, specs: list[tuple]) -> list:
    """Force the user to the free tier and insert one Front per spec.

    specs is a list of (created_at, started_at, ended_at|None). ``created_at``
    is set explicitly: it is the row-insertion time the retention rule keys
    off, and the real API / import paths only ever stamp it "now", so a test
    has to backdate it directly to exercise the rule. Returns the new front
    ids in the same order.
    """

    async def _run() -> list:
        from sqlalchemy import select

        from sheaf.crypto import blind_index
        from sheaf.models.front import Front
        from sheaf.models.system import System
        from sheaf.models.user import User, UserTier

        engine, async_session = _engine_session()
        ids = []
        async with async_session() as db:
            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
            ).scalar_one()
            user.tier = UserTier.FREE
            system = (
                await db.execute(select(System).where(System.user_id == user.id))
            ).scalar_one()
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


def _run_prune() -> dict:
    async def _run() -> dict:
        from sheaf.services.front_retention import prune_free_tier_fronts

        engine, async_session = _engine_session()
        async with async_session() as db:
            result = await prune_free_tier_fronts(db)
            await db.commit()
        await engine.dispose()
        return result

    return asyncio.run(_run())


def test_prune_keys_off_insert_and_end_time(client: httpx.Client, monkeypatch):
    """Prune only a front that was inserted long ago AND ended long ago AND is
    closed. Five scenarios pin every arm of the rule; case (1) is the
    regression guard for the 2026-06-23 imported-history incident."""
    from sheaf.config import SheafMode, settings

    monkeypatch.setattr(settings, "sheaf_mode", SheafMode.SAAS)
    monkeypatch.setattr(settings, "free_tier_front_retention_days", 30)

    email = _register(client)
    now = datetime.now(UTC)

    imported, old_native, long_recent, still_open, recent = _set_tier_free_and_seed(
        email,
        [
            # (1) INCIDENT REGRESSION GUARD - imported historical front.
            # Inserted now (created_at = now), but carries a ~2-year-old
            # real-world start/end. It ended long ago yet was inserted just
            # now, so it MUST SURVIVE. Keying off started_at or ended_at alone
            # deletes this; the created_at clause is the whole point.
            (now, now - timedelta(days=730), now - timedelta(days=700)),
            # (2) Genuinely old native front: inserted ~200d ago, ended ~190d
            # ago. Old on every axis -> pruned.
            (
                now - timedelta(days=200),
                now - timedelta(days=200),
                now - timedelta(days=190),
            ),
            # (3) Long native front that only just ended: inserted ~200d ago
            # but ended yesterday (inside the window). Recent activity -> MUST
            # SURVIVE.
            (
                now - timedelta(days=200),
                now - timedelta(days=200),
                now - timedelta(days=1),
            ),
            # (4) Open front (ended_at NULL): never pruned while ongoing, even
            # though it was inserted long ago.
            (now - timedelta(days=200), now - timedelta(days=200), None),
            # (5) Recently created and recently ended: inside the window on
            # every axis -> survives.
            (now, now - timedelta(days=5), now - timedelta(days=3)),
        ],
    )

    result = _run_prune()
    # At least our one genuinely-old front (case 2) is removed. items_processed
    # is a global count across all free-tier users, so only assert it ran;
    # per-user survival is checked against this account's fronts below.
    assert result["items_processed"] >= 1

    surviving = _surviving_front_ids(email)
    assert imported in surviving, (
        "REGRESSION: freshly-imported historical front was pruned - this is the "
        "2026-06-23 incident. created_at (insert time) must protect it."
    )
    assert long_recent in surviving, "long front that ended yesterday was wrongly pruned"
    assert still_open in surviving, "open front must never be pruned"
    assert recent in surviving, "recently created + ended front was wrongly pruned"
    assert old_native not in surviving, (
        "front inserted and ended long ago (all axes old) should be pruned"
    )


@pytest.mark.selfhosted
def test_prune_noop_when_not_saas(client: httpx.Client):
    """Self-hosted instances never prune: the mode gate short-circuits."""
    from sheaf.config import SheafMode, settings

    # Belt and braces: the default test stack is self-hosted, but assert the
    # gate rather than the ambient config.
    assert settings.sheaf_mode != SheafMode.SAAS
    result = _run_prune()
    assert result == {"items_processed": 0}
