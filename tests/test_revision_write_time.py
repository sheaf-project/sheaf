"""Tests for write-time revision capping + debounce in capture_revision.

These drive `capture_revision` directly against the test database rather than
through the edit endpoints. The debounce window and the "landed N minutes ago"
timing can't be controlled through the black-box server (its
revision_debounce_minutes is fixed by the running config, and we can't wait out
a real window), so we exercise the service function in-process where we can set
settings.revision_debounce_minutes and backdate inserted_at deterministically.
The real capture path (encryption via encrypt(), image-key extraction, the
count-cap trim) is fully exercised. Entities are created through the API so the
user/system/entry rows are real.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from sheaf.config import settings


def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
    return create_async_engine(db_url)


def _register(client: httpx.Client, prefix: str = "wt") -> str:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return email


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

    asyncio.run(_run())


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

    asyncio.run(_run())


async def _load_user_system(db, email: str):
    from sqlalchemy import select

    from sheaf.crypto import blind_index
    from sheaf.models.system import System
    from sheaf.models.user import User

    user = (
        await db.execute(select(User).where(User.email_hash == blind_index(email)))
    ).scalar_one()
    system = (
        await db.execute(select(System).where(System.user_id == user.id))
    ).scalar_one()
    return user, system


def _make_entry(client: httpx.Client) -> str:
    """Create a journal entry (no revisions yet) and return its id."""
    return client.post("/v1/journals", json={"title": "T0", "body": "B0"}).json()["id"]


def _revisions_for(entry_id: str) -> list:
    """Load all revision rows for a journal entry, newest inserted_at first."""
    from sqlalchemy import select

    async def _run() -> list:
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.content_revision import ContentRevision

        engine = _engine()
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                rows = (
                    await db.execute(
                        select(ContentRevision)
                        .where(
                            ContentRevision.target_id == uuid.UUID(entry_id),
                            ContentRevision.target_type == "journal_entry",
                        )
                        .order_by(ContentRevision.inserted_at.desc())
                    )
                ).scalars().all()
                # Detach copies of the fields we assert on.
                return [
                    {
                        "id": r.id,
                        "body": r.body,
                        "title": r.title,
                        "pinned_at": r.pinned_at,
                        "inserted_at": r.inserted_at,
                    }
                    for r in rows
                ]
        finally:
            await engine.dispose()

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Task B: write-time count cap
# ---------------------------------------------------------------------------


def test_append_past_cap_trims_oldest_unpinned_keeps_pins(
    client: httpx.Client, monkeypatch
):
    """Appending past the effective count cap trims the oldest UNPINNED
    revision and never touches a pinned one."""
    email = _register(client, "wtcap")
    _set_user_tier(email, "free")
    # Effective cap = min(override, tier free 50) = 3. Auto-pin off so the
    # cap runs in isolation; we pin one row by hand below.
    _set_system_fields(email, journal_max_revisions=3, auto_pin_first_revision=False)
    entry_id = _make_entry(client)

    # Disable debounce so every capture appends a distinct row.
    monkeypatch.setattr(settings, "revision_debounce_minutes", 0)

    async def _run() -> dict:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.content_revision import ContentRevision
        from sheaf.services.journals import capture_revision

        engine = _engine()
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                user, system = await _load_user_system(db, email)
                now = datetime.now(UTC)
                # Seed history directly: one PINNED (oldest) + three unpinned,
                # with deterministic, strictly-increasing inserted_at.
                pinned = ContentRevision(
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    body="pinned-oldest",
                    inserted_at=now - timedelta(minutes=50),
                    pinned_at=now - timedelta(minutes=50),
                )
                old0 = ContentRevision(
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    body="unpinned-0",
                    inserted_at=now - timedelta(minutes=40),
                )
                old1 = ContentRevision(
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    body="unpinned-1",
                    inserted_at=now - timedelta(minutes=30),
                )
                old2 = ContentRevision(
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    body="unpinned-2",
                    inserted_at=now - timedelta(minutes=20),
                )
                db.add_all([pinned, old0, old1, old2])
                await db.commit()

                # Unpinned count is now 3 (at cap). This append pushes to 4,
                # so the oldest unpinned (old0) must be trimmed.
                new_rev = await capture_revision(
                    db=db,
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    user=user,
                    system_id=system.id,
                    title=None,
                    body="fresh-outgoing",
                )
                await db.commit()

                rows = (
                    await db.execute(
                        select(ContentRevision).where(
                            ContentRevision.target_id == uuid.UUID(entry_id)
                        )
                    )
                ).scalars().all()
                return {
                    "ids": {str(r.id) for r in rows},
                    "pinned_id": str(pinned.id),
                    "old0_id": str(old0.id),
                    "old1_id": str(old1.id),
                    "old2_id": str(old2.id),
                    "new_id": str(new_rev.id),
                    "pinned_present": any(
                        r.id == pinned.id for r in rows
                    ),
                }
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    # old0 (oldest unpinned) trimmed; pinned + old1 + old2 + new survive.
    assert result["old0_id"] not in result["ids"]
    assert result["pinned_id"] in result["ids"]
    assert result["old1_id"] in result["ids"]
    assert result["old2_id"] in result["ids"]
    assert result["new_id"] in result["ids"]
    # Total = 1 pinned + 3 unpinned (cap).
    assert len(result["ids"]) == 4
    assert result["pinned_present"]


# ---------------------------------------------------------------------------
# Task C: debounce / checkpoint semantics
# ---------------------------------------------------------------------------


def test_two_edits_within_window_collapse_to_one_revision(
    client: httpx.Client, monkeypatch
):
    """Two edits inside the debounce window produce ONE revision holding the
    later outgoing content, and its inserted_at does not move."""
    email = _register(client, "wtdebounce")
    # Auto-pin off so the first captured revision is unpinned (replaceable).
    _set_system_fields(email, auto_pin_first_revision=False)
    entry_id = _make_entry(client)

    monkeypatch.setattr(settings, "revision_debounce_minutes", 5)

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.services.journals import capture_revision

        engine = _engine()
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                user, system = await _load_user_system(db, email)
                for body in ("outgoing-1", "outgoing-2"):
                    await capture_revision(
                        db=db,
                        target_type="journal_entry",
                        target_id=uuid.UUID(entry_id),
                        user=user,
                        system_id=system.id,
                        title="edit-title",
                        body=body,
                    )
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())

    from sheaf.crypto import decrypt
    from sheaf.encrypted_fields import revision_body_aad

    rows = _revisions_for(entry_id)
    assert len(rows) == 1
    # The single revision holds the LATER outgoing content (encrypted at rest,
    # bound to the revision row that was replaced in place).
    assert decrypt(
        rows[0]["body"], aad=revision_body_aad(rows[0]["id"])
    ) == "outgoing-2"


def test_replace_does_not_move_inserted_at(client: httpx.Client, monkeypatch):
    """A debounce replace must leave inserted_at anchored to the first save."""
    email = _register(client, "wtanchor")
    _set_system_fields(email, auto_pin_first_revision=False)
    entry_id = _make_entry(client)

    monkeypatch.setattr(settings, "revision_debounce_minutes", 5)

    async def _run() -> dict:
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.services.journals import capture_revision

        engine = _engine()
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                user, system = await _load_user_system(db, email)
                first = await capture_revision(
                    db=db,
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    user=user,
                    system_id=system.id,
                    title=None,
                    body="first",
                )
                await db.flush()
                # Refresh so the server-default inserted_at is materialised on
                # the ORM object (async sessions won't lazy-load it on access).
                await db.refresh(first)
                first_inserted = first.inserted_at
                replaced = await capture_revision(
                    db=db,
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    user=user,
                    system_id=system.id,
                    title=None,
                    body="second",
                )
                await db.commit()
                return {
                    "same_row": first.id == replaced.id,
                    "inserted_unchanged": replaced.inserted_at == first_inserted,
                }
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    assert result["same_row"]
    assert result["inserted_unchanged"]


def test_two_edits_past_window_produce_two_revisions(
    client: httpx.Client, monkeypatch
):
    """Two edits more than the debounce window apart produce TWO revisions."""
    email = _register(client, "wtwindow")
    _set_system_fields(email, auto_pin_first_revision=False)
    entry_id = _make_entry(client)

    monkeypatch.setattr(settings, "revision_debounce_minutes", 5)

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.services.journals import capture_revision

        engine = _engine()
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                user, system = await _load_user_system(db, email)
                first = await capture_revision(
                    db=db,
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    user=user,
                    system_id=system.id,
                    title=None,
                    body="first",
                )
                await db.flush()
                # Backdate the first checkpoint past the window so the next
                # save can't fold into it.
                first.inserted_at = datetime.now(UTC) - timedelta(minutes=10)
                await db.flush()
                await capture_revision(
                    db=db,
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    user=user,
                    system_id=system.id,
                    title=None,
                    body="second",
                )
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())

    rows = _revisions_for(entry_id)
    assert len(rows) == 2


def test_pinned_most_recent_is_never_replaced(client: httpx.Client, monkeypatch):
    """When the newest revision is pinned, a save inside the window appends a
    new row rather than mutating the pin."""
    email = _register(client, "wtpinned")
    _set_system_fields(email, auto_pin_first_revision=False)
    entry_id = _make_entry(client)

    monkeypatch.setattr(settings, "revision_debounce_minutes", 5)

    async def _run() -> dict:
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.services.journals import capture_revision

        engine = _engine()
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                user, system = await _load_user_system(db, email)
                first = await capture_revision(
                    db=db,
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    user=user,
                    system_id=system.id,
                    title=None,
                    body="pinned-body",
                )
                await db.flush()
                # Pin the newest (and only) revision.
                first.pinned_at = datetime.now(UTC)
                await db.flush()
                second = await capture_revision(
                    db=db,
                    target_type="journal_entry",
                    target_id=uuid.UUID(entry_id),
                    user=user,
                    system_id=system.id,
                    title=None,
                    body="new-body",
                )
                await db.commit()
                return {
                    "appended_new_row": second.id != first.id,
                    "pinned_id": str(first.id),
                }
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    assert result["appended_new_row"]

    from sheaf.crypto import decrypt
    from sheaf.encrypted_fields import revision_body_aad

    rows = _revisions_for(entry_id)
    assert len(rows) == 2
    # The pinned row is untouched (still holds its original body).
    pinned_rows = [r for r in rows if r["pinned_at"] is not None]
    assert len(pinned_rows) == 1
    assert str(pinned_rows[0]["id"]) == result["pinned_id"]
    assert decrypt(
        pinned_rows[0]["body"], aad=revision_body_aad(pinned_rows[0]["id"])
    ) == "pinned-body"
