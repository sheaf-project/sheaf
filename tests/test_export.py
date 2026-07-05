import httpx


def test_export_empty_system(auth_client: httpx.Client):
    resp = auth_client.get("/v1/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "2"
    assert data["system"]["name"] == "My System"
    assert data["members"] == []
    assert data["fronts"] == []


def test_export_with_data(auth_client: httpx.Client):
    # Create a member
    member_resp = auth_client.post(
        "/v1/members", json={"name": "ExportMember", "pronouns": "they/them"},
    )
    member_id = member_resp.json()["id"]

    # Create a front
    auth_client.post("/v1/fronts", json={"member_ids": [member_id]})

    # Create a group with the member
    group_resp = auth_client.post("/v1/groups", json={"name": "ExportGroup"})
    group_id = group_resp.json()["id"]
    auth_client.put(
        f"/v1/groups/{group_id}/members", json={"member_ids": [member_id]},
    )

    # Create a tag
    auth_client.post("/v1/tags", json={"name": "export-tag"})

    # Export
    resp = auth_client.get("/v1/export")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["members"]) == 1
    assert data["members"][0]["name"] == "ExportMember"
    assert len(data["fronts"]) == 1
    assert member_id in data["fronts"][0]["member_ids"]
    assert len(data["groups"]) == 1
    assert member_id in data["groups"][0]["member_ids"]
    assert len(data["tags"]) == 1


def test_export_survives_unreadable_encrypted_field(auth_client: httpx.Client):
    """A single field of unreadable ciphertext must not sink the whole export.

    Seed a front whose custom_status holds a non-ciphertext string (the exact
    shape that crashed a live export with 'nacl ... nonce must be exactly 24
    bytes long') and assert the export still returns 200 with that one field
    null, while every other field exports normally.
    """
    import asyncio
    import os
    import uuid as _uuid

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from sheaf.config import settings
    from sheaf.models.front import Front

    member_resp = auth_client.post("/v1/members", json={"name": "SurvivorMember"})
    assert member_resp.status_code == 201, member_resp.text
    member_id = member_resp.json()["id"]

    front_resp = auth_client.post("/v1/fronts", json={"member_ids": [member_id]})
    assert front_resp.status_code in (200, 201), front_resp.text
    front_id = _uuid.UUID(front_resp.json()["id"])

    # Corrupt the front's custom_status directly: store a plain string that is
    # not valid ciphertext, so decrypt() raises when the export reaches it.
    async def _corrupt() -> None:
        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with async_session() as db:
                front = (
                    await db.execute(select(Front).where(Front.id == front_id))
                ).scalar_one()
                front.custom_status = "not-encrypted"
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_corrupt())

    resp = auth_client.get("/v1/export")
    # The whole export must not 500 over the one bad field.
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # The unreadable field exports as null...
    assert len(data["fronts"]) == 1
    assert data["fronts"][0]["custom_status"] is None
    # ...and everything else still exports normally.
    assert len(data["members"]) == 1
    assert data["members"][0]["name"] == "SurvivorMember"
    assert member_id in data["fronts"][0]["member_ids"]


def test_export_placeholder_for_unreadable_required_field(auth_client: httpx.Client):
    """An unreadable *required* field (a member name, routed through the
    shared member_plaintext helper) must not 500 the export either. It exports
    as the placeholder rather than null - a null would risk a NOT NULL
    violation on re-import - while the rest of the export stays intact.
    """
    import asyncio
    import os
    import uuid as _uuid

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from sheaf.config import settings
    from sheaf.models.member import Member

    # A readable member so we can confirm the rest of the export survives.
    good_resp = auth_client.post("/v1/members", json={"name": "ReadableMember"})
    assert good_resp.status_code == 201, good_resp.text

    bad_resp = auth_client.post("/v1/members", json={"name": "WillBeCorrupted"})
    assert bad_resp.status_code == 201, bad_resp.text
    bad_id = _uuid.UUID(bad_resp.json()["id"])

    # Corrupt the member's encrypted name directly: a plain string that is not
    # valid ciphertext, so member_plaintext -> decrypt raises during export.
    async def _corrupt() -> None:
        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with async_session() as db:
                member = (
                    await db.execute(select(Member).where(Member.id == bad_id))
                ).scalar_one()
                member.name = "not-encrypted"
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_corrupt())

    resp = auth_client.get("/v1/export")
    # The whole export must not 500 over the one unreadable required field.
    assert resp.status_code == 200, resp.text
    data = resp.json()

    names = {m["name"] for m in data["members"]}
    # Corrupted required field falls back to the placeholder, not null...
    assert "[unreadable]" in names
    assert None not in names
    # ...and the readable member still exports normally.
    assert "ReadableMember" in names
    assert len(data["members"]) == 2
