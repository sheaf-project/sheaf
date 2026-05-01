"""Tests for revision-history retention + tier-cap loosening + tier-downgrade notice."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx


def _register(client: httpx.Client) -> str:
    email = f"retention-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return email


def _set_user_tier_via_db(user_email: str, tier: str) -> None:
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.user import User

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            email_hash = blind_index(user_email)
            user = (
                await db.execute(select(User).where(User.email_hash == email_hash))
            ).scalar_one()
            user.tier = tier
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _set_system_safety_via_db(user_email: str, **fields) -> None:
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.system import System
        from sheaf.models.user import User

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            email_hash = blind_index(user_email)
            user = (
                await db.execute(select(User).where(User.email_hash == email_hash))
            ).scalar_one()
            system = (
                await db.execute(select(System).where(System.user_id == user.id))
            ).scalar_one()
            for k, v in fields.items():
                setattr(system, k, v)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Read endpoint: tier caps + override + effective
# ---------------------------------------------------------------------------


def test_get_retention_self_hosted_default(auth_client: httpx.Client):
    """Default user tier in test env is self_hosted → unlimited (0/0)."""
    resp = auth_client.get("/v1/retention")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier_max_revisions"] == 0
    assert body["tier_max_days"] == 0
    assert body["override_revisions"] is None
    assert body["override_days"] is None
    assert body["trim_notice"] is None


def test_get_retention_free_tier_caps(client: httpx.Client):
    email = _register(client)
    _set_user_tier_via_db(email, "free")
    body = client.get("/v1/retention").json()
    assert body["tier_max_revisions"] == 10  # journal_max_revisions_free
    assert body["tier_max_days"] == 30  # journal_max_revision_days_free
    assert body["effective_max_revisions"] == 10
    assert body["effective_max_days"] == 30


# ---------------------------------------------------------------------------
# Override behavior
# ---------------------------------------------------------------------------


def test_override_below_tier_max_applies_immediately_when_no_grace(
    client: httpx.Client,
):
    """Reductions are loosening, but with grace=0 they apply immediately."""
    email = _register(client)
    _set_user_tier_via_db(email, "free")
    # Tightening from None (= tier max 10) to 5 is a loosening; grace=0 means
    # it lands immediately anyway.
    resp = client.patch("/v1/retention", json={"max_revisions": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["override_revisions"] == 5
    assert body["effective_max_revisions"] == 5


def test_override_reduction_with_grace_is_deferred(client: httpx.Client):
    email = _register(client)
    _set_user_tier_via_db(email, "free")
    _set_system_safety_via_db(email, safety_grace_period_days=7)

    # Reduction from None -> 5 should be deferred.
    resp = client.patch("/v1/retention", json={"max_revisions": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Override hasn't applied yet — still showing tier max as effective.
    assert body["override_revisions"] is None
    assert body["effective_max_revisions"] == 10

    # Pending change should be visible in the system safety endpoint.
    pending = client.get("/v1/system/safety").json()["pending_changes"]
    assert any("journal_max_revisions" in p["changes"] for p in pending)


def test_override_above_tier_max_is_rejected(client: httpx.Client):
    email = _register(client)
    _set_user_tier_via_db(email, "free")
    resp = client.patch("/v1/retention", json={"max_revisions": 9999})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# tier_revision_caps + on_tier_change
# ---------------------------------------------------------------------------


def test_on_tier_change_creates_notice_for_downgrade():
    """Downgrading PLUS -> FREE should create a pending RetentionTrimNotice."""
    from sheaf.crypto import blind_index

    email = f"tierchange-{uuid.uuid4().hex[:8]}@sheaf.dev"
    # Register the user via the API first so they have a system row.
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        resp = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert resp.status_code == 201

    async def _run() -> dict:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.retention_trim_notice import (
            RetentionTrimNotice,
            RetentionTrimStatus,
        )
        from sheaf.models.user import User, UserTier
        from sheaf.services.retention import on_tier_change

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with async_session() as db:
                email_hash = blind_index(email)
                user = (
                    await db.execute(
                        select(User).where(User.email_hash == email_hash)
                    )
                ).scalar_one()
                # Move them to PLUS first so the downgrade actually reduces caps.
                user.tier = UserTier.PLUS
                await db.commit()

                # Now downgrade to FREE.
                notice = await on_tier_change(
                    user, UserTier.PLUS, UserTier.FREE, db
                )
                await db.commit()
                assert notice is not None

                # Check there's exactly one pending notice.
                rows = (
                    await db.execute(
                        select(RetentionTrimNotice).where(
                            RetentionTrimNotice.user_id == user.id,
                            RetentionTrimNotice.status
                            == RetentionTrimStatus.PENDING,
                        )
                    )
                ).scalars().all()
                return {
                    "count": len(rows),
                    "from_tier": rows[0].from_tier if rows else None,
                    "to_tier": rows[0].to_tier if rows else None,
                }
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    assert result["count"] == 1
    assert result["from_tier"] == "plus"
    assert result["to_tier"] == "free"


def test_on_tier_change_upgrade_cancels_existing_notice():
    """Re-upgrading should cancel any pending downgrade notice."""
    from sheaf.crypto import blind_index

    email = f"tierup-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        resp = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert resp.status_code == 201

    async def _run() -> dict:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.retention_trim_notice import (
            RetentionTrimNotice,
            RetentionTrimStatus,
        )
        from sheaf.models.user import User, UserTier
        from sheaf.services.retention import on_tier_change

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with async_session() as db:
                email_hash = blind_index(email)
                user = (
                    await db.execute(
                        select(User).where(User.email_hash == email_hash)
                    )
                ).scalar_one()
                user.tier = UserTier.PLUS
                await db.commit()

                # Downgrade — creates notice
                await on_tier_change(user, UserTier.PLUS, UserTier.FREE, db)
                await db.commit()

                # Upgrade — cancels notice
                user.tier = UserTier.PLUS
                await on_tier_change(user, UserTier.FREE, UserTier.PLUS, db)
                await db.commit()

                pending = (
                    await db.execute(
                        select(RetentionTrimNotice).where(
                            RetentionTrimNotice.user_id == user.id,
                            RetentionTrimNotice.status
                            == RetentionTrimStatus.PENDING,
                        )
                    )
                ).scalars().all()
                cancelled = (
                    await db.execute(
                        select(RetentionTrimNotice).where(
                            RetentionTrimNotice.user_id == user.id,
                            RetentionTrimNotice.status
                            == RetentionTrimStatus.CANCELLED,
                        )
                    )
                ).scalars().all()
                return {
                    "pending": len(pending),
                    "cancelled": len(cancelled),
                }
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    assert result["pending"] == 0
    assert result["cancelled"] == 1


# ---------------------------------------------------------------------------
# GC sweep trims to caps
# ---------------------------------------------------------------------------


def test_gc_revisions_trims_to_count_cap(client: httpx.Client):
    email = _register(client)
    _set_user_tier_via_db(email, "free")  # cap = 10
    # This test is about the rolling count cap in isolation; opt out of
    # auto-pin so the trim count matches what the cap would do alone.
    _set_system_safety_via_db(email, auto_pin_first_revision=False)

    # Create one entry and edit it 15 times to generate 15 revisions.
    entry = client.post("/v1/journals", json={"body": "v0"}).json()
    for i in range(1, 16):
        client.patch(
            f"/v1/journals/{entry['id']}", json={"body": f"v{i}"}
        )

    revs_before = client.get(
        f"/v1/journals/{entry['id']}/revisions"
    ).json()
    assert len(revs_before) == 15

    # Run the GC job directly.
    async def _run_gc() -> int:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.services.retention import gc_revisions

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with async_session() as db:
                result = await gc_revisions(db)
                await db.commit()
                return result["items_processed"]
        finally:
            await engine.dispose()

    deleted = asyncio.run(_run_gc())
    assert deleted >= 5

    revs_after = client.get(
        f"/v1/journals/{entry['id']}/revisions"
    ).json()
    assert len(revs_after) == 10


def test_gc_revisions_grace_window_defers_trim():
    """Active trim notice should keep pre-downgrade caps until effective_at."""
    from sheaf.crypto import blind_index

    email = f"gcgrace-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        resp = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert resp.status_code == 201
        c.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"

        # Create 15 revisions while on PLUS (cap 100, no trimming yet).
        # Disable auto-pin so we exercise the rolling cap in isolation.
        _set_user_tier_via_db(email, "plus")
        _set_system_safety_via_db(email, auto_pin_first_revision=False)
        entry = c.post("/v1/journals", json={"body": "v0"}).json()
        for i in range(1, 16):
            c.patch(f"/v1/journals/{entry['id']}", json={"body": f"v{i}"})

        # Downgrade through on_tier_change to create a notice
        async def _downgrade() -> None:
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
            from sqlalchemy.orm import sessionmaker

            from sheaf.config import settings
            from sheaf.models.user import User, UserTier
            from sheaf.services.retention import on_tier_change

            db_url = (
                os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
            )
            engine = create_async_engine(db_url)
            async_session = sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            try:
                async with async_session() as db:
                    email_hash = blind_index(email)
                    user = (
                        await db.execute(
                            select(User).where(User.email_hash == email_hash)
                        )
                    ).scalar_one()
                    await on_tier_change(
                        user, UserTier.PLUS, UserTier.FREE, db
                    )
                    user.tier = UserTier.FREE
                    await db.commit()
            finally:
                await engine.dispose()

        asyncio.run(_downgrade())

        # Run GC — should NOT trim because notice's effective_at is future.
        async def _run_gc() -> None:
            from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
            from sqlalchemy.orm import sessionmaker

            from sheaf.config import settings
            from sheaf.services.retention import gc_revisions

            db_url = (
                os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
            )
            engine = create_async_engine(db_url)
            async_session = sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            try:
                async with async_session() as db:
                    await gc_revisions(db)
                    await db.commit()
            finally:
                await engine.dispose()

        asyncio.run(_run_gc())

        revs = c.get(f"/v1/journals/{entry['id']}/revisions").json()
        # All 15 should still be present during grace.
        assert len(revs) == 15

        # Backdate the notice and re-run GC — now it trims.
        async def _backdate() -> None:
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
            from sqlalchemy.orm import sessionmaker

            from sheaf.config import settings
            from sheaf.models.retention_trim_notice import RetentionTrimNotice
            from sheaf.models.user import User

            db_url = (
                os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
            )
            engine = create_async_engine(db_url)
            async_session = sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            try:
                async with async_session() as db:
                    email_hash = blind_index(email)
                    user = (
                        await db.execute(
                            select(User).where(User.email_hash == email_hash)
                        )
                    ).scalar_one()
                    notice = (
                        await db.execute(
                            select(RetentionTrimNotice).where(
                                RetentionTrimNotice.user_id == user.id
                            )
                        )
                    ).scalar_one()
                    notice.effective_at = datetime.now(UTC) - timedelta(days=1)
                    await db.commit()
            finally:
                await engine.dispose()

        asyncio.run(_backdate())
        asyncio.run(_run_gc())

        revs_after = c.get(f"/v1/journals/{entry['id']}/revisions").json()
        # Now trimmed to FREE cap (10).
        assert len(revs_after) == 10
