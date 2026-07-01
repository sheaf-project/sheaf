"""Front-history retention pruning (aaS free tier).

Retention keys off a front's END time, not its start: a long-running front
(months/years) is kept for the full window after it closes, and is never
pruned while still open. These tests pin that, since keying off start time
(the old behaviour) would silently delete a months-long front the instant
it ended, even though it was the most recently active period.

The prune is mode-gated (aaS only), so the service is driven in-process with
sheaf_mode monkeypatched to SAAS and the user forced to the free tier in the
DB. Fronts are seeded directly with explicit timestamps because the API only
ever stamps "now".
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

    specs is a list of (started_at, ended_at|None); returns the new front
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
            for started, ended in specs:
                front = Front(system_id=system.id, started_at=started, ended_at=ended)
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


def test_prune_keys_off_end_time_not_start(client: httpx.Client, monkeypatch):
    """A months-long front that only just ended survives the window; one
    that ended outside it is pruned; an open one is never touched."""
    from sheaf.config import SheafMode, settings

    monkeypatch.setattr(settings, "sheaf_mode", SheafMode.SAAS)
    monkeypatch.setattr(settings, "free_tier_front_retention_days", 30)

    email = _register(client)
    now = datetime.now(UTC)
    six_months_ago = now - timedelta(days=180)

    recent_long, stale_long, still_open = _set_tier_free_and_seed(
        email,
        [
            # Started 6 months ago, ended yesterday: active within the
            # window, must survive (the old started_at logic deleted this).
            (six_months_ago, now - timedelta(days=1)),
            # Started 6 months ago, ended 60 days ago: outside the window.
            (six_months_ago, now - timedelta(days=60)),
            # Started 6 months ago, still open: never pruned while ongoing.
            (six_months_ago, None),
        ],
    )

    result = _run_prune()
    assert result["items_processed"] >= 1

    surviving = _surviving_front_ids(email)
    assert recent_long in surviving, "front that ended yesterday was wrongly pruned"
    assert still_open in surviving, "open front must never be pruned"
    assert stale_long not in surviving, "front that ended 60 days ago should be pruned"


@pytest.mark.selfhosted
def test_prune_noop_when_not_saas(client: httpx.Client):
    """Self-hosted instances never prune: the mode gate short-circuits."""
    from sheaf.config import SheafMode, settings

    # Belt and braces: the default test stack is self-hosted, but assert the
    # gate rather than the ambient config.
    assert settings.sheaf_mode != SheafMode.SAAS
    result = _run_prune()
    assert result == {"items_processed": 0}
