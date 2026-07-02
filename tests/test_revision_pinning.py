"""Tests for revision pinning: per-target caps, unpin grace, GC exemption,
auto-pin first revision."""

import asyncio
import os
import uuid

import httpx

# ---------------------------------------------------------------------------
# Helpers — DB pokes mirror the patterns in test_revision_retention.py
# ---------------------------------------------------------------------------


def _register(client: httpx.Client, prefix: str = "pin") -> str:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return email


def _run_async(coro):
    return asyncio.run(coro)


def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    from sheaf.config import settings

    db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
    return create_async_engine(db_url)


def _set_user_tier(email: str, tier: str) -> None:
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.user import User

        engine = _engine()
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
            ).scalar_one()
            user.tier = tier
            await db.commit()
        await engine.dispose()

    _run_async(_run())


def _set_system_fields(email: str, **fields) -> None:
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.system import System
        from sheaf.models.user import User

        engine = _engine()
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
            ).scalar_one()
            system = (
                await db.execute(select(System).where(System.user_id == user.id))
            ).scalar_one()
            for k, v in fields.items():
                setattr(system, k, v)
            await db.commit()
        await engine.dispose()

    _run_async(_run())


def _create_entry_with_revisions(
    client: httpx.Client, count: int
) -> tuple[dict, list[dict]]:
    """Create a journal entry then edit it `count` times. Returns (entry, revisions)."""
    entry = client.post(
        "/v1/journals", json={"title": "T0", "body": "B0"}
    ).json()
    for i in range(count):
        client.patch(
            f"/v1/journals/{entry['id']}",
            json={"title": f"T{i + 1}", "body": f"B{i + 1}"},
        )
    revs = client.get(f"/v1/journals/{entry['id']}/revisions").json()
    return entry, revs


# ---------------------------------------------------------------------------
# Auto-pin first revision
# ---------------------------------------------------------------------------


def test_auto_pin_first_revision_default_on(auth_client: httpx.Client):
    """Default auto_pin_first_revision=True — first edit's captured revision is pinned."""
    entry, revs = _create_entry_with_revisions(auth_client, 1)
    assert len(revs) == 1
    assert revs[0]["pinned_at"] is not None


def test_auto_pin_disabled_no_pin(client: httpx.Client):
    email = _register(client, "autopin")
    _set_system_fields(email, auto_pin_first_revision=False)
    _, revs = _create_entry_with_revisions(client, 1)
    assert len(revs) == 1
    assert revs[0]["pinned_at"] is None


def test_auto_pin_only_first_not_subsequent(auth_client: httpx.Client):
    _, revs = _create_entry_with_revisions(auth_client, 3)
    pinned = [r for r in revs if r["pinned_at"] is not None]
    assert len(pinned) == 1


# ---------------------------------------------------------------------------
# Pin endpoint
# ---------------------------------------------------------------------------


def test_pin_unpinned_revision(client: httpx.Client):
    email = _register(client, "pinok")
    _set_system_fields(email, auto_pin_first_revision=False)
    entry, revs = _create_entry_with_revisions(client, 2)
    target = revs[0]
    assert target["pinned_at"] is None

    resp = client.post(
        f"/v1/journals/{entry['id']}/pin-revision",
        json={"revision_id": target["id"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pinned_at"] is not None


def test_pin_already_pinned_409(auth_client: httpx.Client):
    entry, revs = _create_entry_with_revisions(auth_client, 1)
    pinned = revs[0]
    resp = auth_client.post(
        f"/v1/journals/{entry['id']}/pin-revision",
        json={"revision_id": pinned["id"]},
    )
    assert resp.status_code == 409


def test_pin_cap_enforced(client: httpx.Client):
    email = _register(client, "pincap")
    _set_user_tier(email, "free")  # free cap = 3
    _set_system_fields(email, auto_pin_first_revision=False)
    entry, revs = _create_entry_with_revisions(client, 5)
    # Pin three — should all succeed.
    for r in revs[:3]:
        resp = client.post(
            f"/v1/journals/{entry['id']}/pin-revision",
            json={"revision_id": r["id"]},
        )
        assert resp.status_code == 200, resp.text
    # Fourth pin — at cap, must reject.
    resp = client.post(
        f"/v1/journals/{entry['id']}/pin-revision",
        json={"revision_id": revs[3]["id"]},
    )
    assert resp.status_code == 409


def test_pin_revision_from_other_target_404(auth_client: httpx.Client):
    """Pinning a revision that belongs to a different entry returns 404."""
    a, a_revs = _create_entry_with_revisions(auth_client, 1)
    b, _ = _create_entry_with_revisions(auth_client, 1)
    # Try pinning A's revision via B's pin endpoint.
    resp = auth_client.post(
        f"/v1/journals/{b['id']}/pin-revision",
        json={"revision_id": a_revs[0]["id"]},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Unpin endpoint
# ---------------------------------------------------------------------------


def test_unpin_when_safety_off_immediate(auth_client: httpx.Client):
    """No safety + auto-pinned first revision → immediate unpin."""
    entry, revs = _create_entry_with_revisions(auth_client, 1)
    pinned = revs[0]
    resp = auth_client.post(
        f"/v1/journals/{entry['id']}/unpin-revision",
        json={"revision_id": pinned["id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revision"] is not None
    assert body["revision"]["pinned_at"] is None
    assert body["pending_action_id"] is None


def test_unpin_when_safety_on_queues_pending(client: httpx.Client):
    email = _register(client, "unpingrace")
    _set_system_fields(
        email,
        safety_grace_period_days=7,
        safety_applies_to_revisions=True,
    )
    entry, revs = _create_entry_with_revisions(client, 1)
    pinned = revs[0]
    resp = client.post(
        f"/v1/journals/{entry['id']}/unpin-revision",
        json={"revision_id": pinned["id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revision"] is None
    assert body["pending_action_id"] is not None
    assert body["finalize_after"] is not None

    # Revision should still be pinned (not yet finalized).
    after = client.get(f"/v1/journals/{entry['id']}/revisions").json()
    assert after[0]["pinned_at"] is not None

    # Pending action should appear in the safety endpoint, type revision_unpin.
    pending = client.get("/v1/system/safety").json()["pending_actions"]
    assert any(p["action_type"] == "revision_unpin" for p in pending)


def test_unpin_unpinned_revision_409(client: httpx.Client):
    email = _register(client, "unpinnone")
    _set_system_fields(email, auto_pin_first_revision=False)
    entry, revs = _create_entry_with_revisions(client, 1)
    resp = client.post(
        f"/v1/journals/{entry['id']}/unpin-revision",
        json={"revision_id": revs[0]["id"]},
    )
    assert resp.status_code == 409


def test_finalize_pending_unpin_clears_pin():
    """The PendingAction finalize for REVISION_UNPIN must clear pinned_at, not delete."""

    async def _run() -> None:
        import json
        from datetime import UTC, datetime

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.crypto import blind_index, encrypt
        from sheaf.models.content_revision import ContentRevision
        from sheaf.models.pending_action import (
            PendingAction,
            PendingActionStatus,
            PendingActionType,
        )
        from sheaf.models.system import System
        from sheaf.models.user import User
        from sheaf.services.system_safety import finalize_pending_action

        # Use a separately-registered user.
        email = f"finalize-{uuid.uuid4().hex[:8]}@sheaf.dev"
        with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
            c.post(
                "/v1/auth/register",
                json={"email": email, "password": "testpassword123"},
            )
            tok = c.post(
                "/v1/auth/login",
                json={"email": email, "password": "testpassword123"},
            ).json()["access_token"]
            c.headers["Authorization"] = f"Bearer {tok}"
            entry, revs = _create_entry_with_revisions(c, 1)

        engine = _engine()
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
            ).scalar_one()
            system = (
                await db.execute(select(System).where(System.user_id == user.id))
            ).scalar_one()
            revision = await db.get(ContentRevision, uuid.UUID(revs[0]["id"]))
            assert revision.pinned_at is not None

            pending = PendingAction(
                system_id=system.id,
                action_type=PendingActionType.REVISION_UNPIN,
                target_id=revision.id,
                # target_label and fronting_member_names are encrypted at rest.
                target_label=encrypt("test pin"),
                requested_at=datetime.now(UTC),
                requested_by_user_id=user.id,
                finalize_after=datetime.now(UTC),
                fronting_member_ids=[],
                fronting_member_names=encrypt(json.dumps([])),
                status=PendingActionStatus.PENDING,
            )
            db.add(pending)
            await db.commit()

            await finalize_pending_action(pending, db)
            await db.commit()

            await db.refresh(revision)
            assert revision.pinned_at is None
            # Critically — the row itself must NOT be deleted.
            still_there = await db.get(ContentRevision, revision.id)
            assert still_there is not None
        await engine.dispose()

    _run_async(_run())


# ---------------------------------------------------------------------------
# GC sweep exemption
# ---------------------------------------------------------------------------


def test_gc_sweep_skips_pinned():
    """gc_revisions must not delete pinned rows even when the count cap is exceeded."""
    # Set up fixture state OUTSIDE the inner async block — the DB pokes use
    # asyncio.run() internally and can't be called from within a running loop.
    email = f"gcpin-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        tok = c.post(
            "/v1/auth/login",
            json={"email": email, "password": "testpassword123"},
        ).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        # Create 6 revisions; the auto-pinned first is pinned.
        entry, _ = _create_entry_with_revisions(c, 5)
        entry_id = entry["id"]

    # Drop user to free tier (cap = 10) and tighten override to 2 so the
    # sweep WILL trim. The pinned first revision must survive.
    _set_user_tier(email, "free")
    _set_system_fields(email, journal_max_revisions=2)

    async def _run() -> None:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.content_revision import ContentRevision
        from sheaf.services.retention import gc_revisions

        engine = _engine()
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            await gc_revisions(db)
            await db.commit()
            # Pinned revision still present.
            pinned_rows = (
                await db.execute(
                    select(ContentRevision).where(
                        ContentRevision.target_id == uuid.UUID(entry_id),
                        ContentRevision.pinned_at.is_not(None),
                    )
                )
            ).scalars().all()
            assert len(pinned_rows) == 1
            # Total revisions should be: 1 pinned + 2 newest unpinned = 3.
            all_rows = (
                await db.execute(
                    select(ContentRevision).where(
                        ContentRevision.target_id == uuid.UUID(entry_id),
                    )
                )
            ).scalars().all()
            assert len(all_rows) == 3
        await engine.dispose()

    _run_async(_run())


# ---------------------------------------------------------------------------
# Bio (member) pinning — sanity check the polymorphic path
# ---------------------------------------------------------------------------


def test_bio_pin_unpin_round_trip(auth_client: httpx.Client):
    member = auth_client.post(
        "/v1/members", json={"name": "Alice", "description": "v0"}
    ).json()
    auth_client.patch(
        f"/v1/members/{member['id']}", json={"description": "v1"}
    )
    revs = auth_client.get(f"/v1/members/{member['id']}/revisions").json()
    assert len(revs) == 1
    assert revs[0]["pinned_at"] is not None  # auto-pin

    unpin = auth_client.post(
        f"/v1/members/{member['id']}/unpin-revision",
        json={"revision_id": revs[0]["id"]},
    )
    assert unpin.status_code == 200, unpin.text

    # Re-pin manually.
    pin = auth_client.post(
        f"/v1/members/{member['id']}/pin-revision",
        json={"revision_id": revs[0]["id"]},
    )
    assert pin.status_code == 200
    assert pin.json()["pinned_at"] is not None
