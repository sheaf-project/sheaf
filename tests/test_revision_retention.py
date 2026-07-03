"""Tests for revision-history retention + tier-cap loosening + tier-downgrade notice."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest


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


def _seed_revisions_via_db(
    *,
    target_type: str,
    target_id: uuid.UUID,
    count: int,
    pinned_indices: set[int] | None = None,
) -> list[uuid.UUID]:
    """Insert `count` ContentRevision rows for one target DIRECTLY.

    Bypasses capture_revision on purpose - the same shape an importer produces
    (rows built straight into the table), which is the only way to reach an
    over-cap state now that capture_revision trims at write time. Each row gets
    a distinct inserted_at (oldest first) so the newest-N sweep is
    deterministic; indices in `pinned_indices` are pinned (exempt from the
    count sweep). Returns the row ids oldest-first.
    """
    from sheaf.crypto import encrypt

    pins = pinned_indices or set()

    async def _run() -> list[uuid.UUID]:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.content_revision import ContentRevision

        anchor = datetime.now(UTC) - timedelta(hours=count)
        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with async_session() as db:
                rows: list[ContentRevision] = []
                for i in range(count):
                    rev = ContentRevision(
                        target_type=target_type,
                        target_id=target_id,
                        body=encrypt(f"seed-{i}"),
                        inserted_at=anchor + timedelta(minutes=i),
                        pinned_at=datetime.now(UTC) if i in pins else None,
                    )
                    db.add(rev)
                    rows.append(rev)
                await db.commit()
                return [r.id for r in rows]
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _revision_ids_for(target_type: str, target_id: uuid.UUID) -> set[uuid.UUID]:
    """Current ContentRevision ids for a target, read straight from the DB."""

    async def _run() -> set[uuid.UUID]:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.content_revision import ContentRevision

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with async_session() as db:
                rows = (
                    await db.execute(
                        select(ContentRevision.id).where(
                            ContentRevision.target_type == target_type,
                            ContentRevision.target_id == target_id,
                        )
                    )
                ).scalars().all()
                return set(rows)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _run_gc() -> int:
    """Run the GC sweep once and return items_processed."""

    async def _run() -> int:
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

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Read endpoint: tier caps + override + effective
# ---------------------------------------------------------------------------


@pytest.mark.selfhosted
def test_get_retention_self_hosted_default(auth_client: httpx.Client):
    """Default user tier in self-hosted mode is self_hosted → unlimited
    (0/0). Skipped under saas, where signups default to free (see
    test_get_retention_free_tier_caps for the free-tier values)."""
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
    assert body["tier_max_revisions"] == 50  # journal_max_revisions_free
    assert body["tier_max_days"] == 30  # journal_max_revision_days_free
    assert body["effective_max_revisions"] == 50
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
    # Tightening from None (= tier max 50) to 5 is a loosening; grace=0 means
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
    # Override hasn't applied yet - still showing tier max as effective.
    assert body["override_revisions"] is None
    assert body["effective_max_revisions"] == 50

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
    """Backstop sweep: gc_revisions trims an over-cap target seeded directly.

    Write-time capping in capture_revision now prevents ever reaching an
    over-cap state through the edit API, so the sweep can only be exercised
    against rows that bypassed it (imports, legacy rows). We seed those rows
    directly and give the user an explicit low override so the cap under test
    is independent of the free-tier default (now 50).
    """
    email = _register(client)
    _set_user_tier_via_db(email, "free")
    # Explicit cap of 5 via the system override (bypasses PATCH validation),
    # independent of the tier default. Auto-pin off so the pinned-exemption
    # case is the one we seed explicitly, not an incidental first-revision pin.
    _set_system_safety_via_db(
        email, journal_max_revisions=5, auto_pin_first_revision=False
    )

    # A real journal entry so gc_revisions discovers the target. Creation does
    # not capture a revision, so the target starts empty.
    entry = client.post("/v1/journals", json={"body": "v0"}).json()
    target_id = uuid.UUID(entry["id"])

    # Seed 15 revisions DIRECTLY (bypassing capture_revision). Pin the oldest
    # to prove pinned rows are exempt from the count sweep.
    seeded = _seed_revisions_via_db(
        target_type="journal_entry",
        target_id=target_id,
        count=15,
        pinned_indices={0},
    )
    assert _revision_ids_for("journal_entry", target_id) == set(seeded)

    deleted = _run_gc()
    # 14 unpinned rows, cap 5 -> keep newest 5, delete 9. (>= because the sweep
    # runs over every user in the shared DB.)
    assert deleted >= 9

    remaining = _revision_ids_for("journal_entry", target_id)
    # Survivors: the pinned oldest (exempt) + the newest 5 unpinned by
    # inserted_at.
    expected = {seeded[0]} | set(seeded[10:15])
    assert remaining == expected


def test_gc_revisions_grace_window_defers_trim():
    """Active trim notice keeps the pre-downgrade cap until effective_at.

    The deferral difference has to come from the tier caps themselves: an
    override applies equally to the pre- and post-downgrade computations, so it
    can never make one higher than the other. We therefore drive it off the
    real PLUS (100) vs FREE (50) tier caps and seed enough rows to sit between
    them - 60. During grace the sweep honors the higher pre-downgrade cap (100)
    and trims nothing; once the notice lapses it trims to the FREE cap (50).
    """
    from sheaf.crypto import blind_index

    email = f"gcgrace-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        resp = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert resp.status_code == 201
        c.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"

        # Start on PLUS and create a real journal entry so gc_revisions finds
        # the target. No override, so the caps under test are the tier caps.
        _set_user_tier_via_db(email, "plus")
        entry = c.post("/v1/journals", json={"body": "v0"}).json()
        target_id = uuid.UUID(entry["id"])

    # Seed 60 revisions DIRECTLY - above the FREE cap (50), below PLUS (100).
    seeded = _seed_revisions_via_db(
        target_type="journal_entry",
        target_id=target_id,
        count=60,
    )
    assert _revision_ids_for("journal_entry", target_id) == set(seeded)

    # Downgrade PLUS -> FREE via on_tier_change to create the pending notice,
    # then land the user on FREE.
    def _downgrade() -> None:
        async def _run() -> None:
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
            from sqlalchemy.orm import sessionmaker

            from sheaf.config import settings
            from sheaf.models.user import User, UserTier
            from sheaf.services.retention import on_tier_change

            db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
            engine = create_async_engine(db_url)
            async_session = sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            try:
                async with async_session() as db:
                    user = (
                        await db.execute(
                            select(User).where(
                                User.email_hash == blind_index(email)
                            )
                        )
                    ).scalar_one()
                    notice = await on_tier_change(
                        user, UserTier.PLUS, UserTier.FREE, db
                    )
                    assert notice is not None  # PLUS(100) -> FREE(50) reduces
                    user.tier = UserTier.FREE
                    await db.commit()
            finally:
                await engine.dispose()

        asyncio.run(_run())

    _downgrade()

    # Sweep during grace: effective_at is in the future, so the pre-downgrade
    # cap (100) is honored and nothing is trimmed.
    _run_gc()
    assert _revision_ids_for("journal_entry", target_id) == set(seeded)

    # Backdate the notice so its window has passed, then sweep again.
    def _backdate() -> None:
        async def _run() -> None:
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
            from sqlalchemy.orm import sessionmaker

            from sheaf.config import settings
            from sheaf.models.retention_trim_notice import RetentionTrimNotice
            from sheaf.models.user import User

            db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
            engine = create_async_engine(db_url)
            async_session = sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            try:
                async with async_session() as db:
                    user = (
                        await db.execute(
                            select(User).where(
                                User.email_hash == blind_index(email)
                            )
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

        asyncio.run(_run())

    _backdate()
    _run_gc()

    # Now trimmed to the FREE cap (50): the oldest 10 by inserted_at are gone,
    # the newest 50 survive.
    remaining = _revision_ids_for("journal_entry", target_id)
    assert remaining == set(seeded[10:60])


# ---------------------------------------------------------------------------
# inserted_at: true row-landing time drives the trim, not source created_at
# ---------------------------------------------------------------------------


def test_new_revision_has_inserted_at(client: httpx.Client):
    """A revision created through the normal edit path gets inserted_at set."""
    _register(client)
    entry = client.post("/v1/journals", json={"body": "v0"}).json()
    client.patch(f"/v1/journals/{entry['id']}", json={"body": "v1"})

    async def _run() -> list:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.content_revision import ContentRevision

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                rows = (
                    await db.execute(
                        select(ContentRevision).where(
                            ContentRevision.target_id == uuid.UUID(entry["id"])
                        )
                    )
                ).scalars().all()
                return [r.inserted_at for r in rows]
        finally:
            await engine.dispose()

    inserted = asyncio.run(_run())
    assert len(inserted) >= 1
    for ts in inserted:
        assert ts is not None
        assert (datetime.now(UTC) - ts) < timedelta(minutes=5)


def test_imported_revision_inserted_at_is_now_not_source():
    """Importers overwrite created_at with the source edit time but must NOT
    touch inserted_at - it defaults to insert time, so imported history is
    counted from when it landed here."""

    async def _run() -> dict:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.content_revision import ContentRevision

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                source = datetime(2020, 1, 1, tzinfo=UTC)
                rev = ContentRevision(
                    target_type="journal_entry",
                    target_id=uuid.uuid4(),
                    body="imported body",
                )
                # Mirror the importer: created_at overwritten from source.
                rev.created_at = source
                db.add(rev)
                await db.commit()
                await db.refresh(rev)
                return {"created": rev.created_at, "inserted": rev.inserted_at}
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    assert result["created"].year == 2020
    assert result["inserted"].year != 2020
    assert (datetime.now(UTC) - result["inserted"]) < timedelta(minutes=5)


def test_trim_keeps_recently_inserted_over_old_source_time():
    """The count trim keeps the most-recently-LANDED revisions. A just-imported
    revision (old created_at, recent inserted_at) survives over one that was
    inserted earlier but authored more recently."""

    async def _run() -> dict:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.content_revision import ContentRevision
        from sheaf.services.retention import _trim_target_group

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                target_id = uuid.uuid4()
                now = datetime.now(UTC)
                # Imported: old source edit time, but only just landed.
                landed_now = ContentRevision(
                    target_type="journal_entry",
                    target_id=target_id,
                    body="imported-old",
                    created_at=now - timedelta(days=100),
                    inserted_at=now,
                )
                # Authored recently but inserted earlier than the import above.
                landed_earlier = ContentRevision(
                    target_type="journal_entry",
                    target_id=target_id,
                    body="landed-earlier",
                    created_at=now - timedelta(days=1),
                    inserted_at=now - timedelta(days=10),
                )
                db.add_all([landed_now, landed_earlier])
                await db.commit()

                deleted = await _trim_target_group(
                    db=db,
                    target_type="journal_entry",
                    target_id=target_id,
                    max_revisions=1,
                    max_days=0,
                )
                await db.commit()

                remaining = (
                    await db.execute(
                        select(ContentRevision.id).where(
                            ContentRevision.target_type == "journal_entry",
                            ContentRevision.target_id == target_id,
                        )
                    )
                ).scalars().all()
                return {
                    "deleted": deleted,
                    "kept": {str(i) for i in remaining},
                    "landed_now": str(landed_now.id),
                    "landed_earlier": str(landed_earlier.id),
                }
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    assert result["deleted"] == 1
    # The just-inserted (imported-old) revision survives; ordering by
    # inserted_at, not created_at, is the whole point of the fix.
    assert result["kept"] == {result["landed_now"]}
