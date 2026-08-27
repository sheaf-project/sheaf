"""The length cap on custom-field values, and its deliberate blind spot.

A value used to be the one piece of user content the API took with no length
at all - the validator checked the value's TYPE and stopped there. It now has
the same 20k ceiling every other long-form field on the instance has.

The blind spot is the point of half of this file: the cap bounds NEW text and
does not retroactively invalidate what was stored before it existed. A value
that is already over it - imported at full length, or written before the cap
shipped - must keep saving unchanged, or somebody carrying one would be locked
out of editing every OTHER field on that member until they destroyed it.
"""

from __future__ import annotations

import asyncio
import os
import uuid as _uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from sheaf.config import settings
from sheaf.models.custom_field import CustomFieldValue
from sheaf.schemas.custom_field import MAX_CUSTOM_FIELD_VALUE_CHARS
from sheaf.services.custom_fields import encrypt_field_value

CAP = MAX_CUSTOM_FIELD_VALUE_CHARS


def _member(client: httpx.Client, name: str = "CapTester") -> str:
    resp = client.post("/v1/members", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _text_field(client: httpx.Client, name: str) -> str:
    resp = client.post("/v1/fields", json={"name": name, "field_type": "text"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _set(client: httpx.Client, member: str, values: list[dict]) -> httpx.Response:
    return client.put(f"/v1/members/{member}/fields", json=values)


def _plant_oversized_value(field_id: str, member_id: str, text: str) -> None:
    """Overwrite a stored value with one that is over the cap.

    Written straight to the database on purpose: the API refuses to create one
    of these, which is exactly why the "already stored" case needs planting to
    be testable at all. The row keeps its id, so the ciphertext still binds to
    the cell it lives in.
    """

    async def _run() -> None:
        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with async_session() as db:
                row = (
                    await db.execute(
                        select(CustomFieldValue).where(
                            CustomFieldValue.field_id == _uuid.UUID(field_id),
                            CustomFieldValue.member_id == _uuid.UUID(member_id),
                        )
                    )
                ).scalar_one()
                row.value = encrypt_field_value(text, row.id)
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_value_at_the_cap_is_accepted(auth_client: httpx.Client):
    member = _member(auth_client)
    field = _text_field(auth_client, "Long answer")

    resp = _set(auth_client, member, [{"field_id": field, "value": "x" * CAP}])
    assert resp.status_code == 200, resp.text


def test_value_over_the_cap_is_rejected_with_the_limit_stated(
    auth_client: httpx.Client,
):
    """One character over is a 400 that says what the limit is - a bare
    "invalid" leaves someone pasting a long answer with nothing to act on."""
    member = _member(auth_client)
    field = _text_field(auth_client, "Too long")

    resp = _set(
        auth_client, member, [{"field_id": field, "value": "x" * (CAP + 1)}]
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "20,000" in detail
    assert "Too long" in detail


def test_the_cap_applies_to_the_envelope_and_to_list_entries(
    auth_client: httpx.Client,
):
    """The web client wraps values as {"v": ...} and a freeform multiselect
    sends a list. An oversized string must not ride in inside either."""
    member = _member(auth_client)
    text_field = _text_field(auth_client, "Enveloped")
    multi = auth_client.post(
        "/v1/fields", json={"name": "Freeform tags", "field_type": "multiselect"}
    ).json()["id"]

    resp = _set(
        auth_client,
        member,
        [{"field_id": text_field, "value": {"v": "x" * (CAP + 1)}}],
    )
    assert resp.status_code == 400, resp.text

    resp = _set(
        auth_client,
        member,
        [{"field_id": multi, "value": ["fine", "x" * (CAP + 1)]}],
    )
    assert resp.status_code == 400, resp.text


def test_an_existing_oversized_value_still_saves_when_unchanged(
    auth_client: httpx.Client,
):
    """The load-bearing one. A member carrying a pre-cap value must stay
    editable: re-submitting that value byte-for-byte alongside a change to
    another field succeeds, and the long value survives intact.
    """
    member = _member(auth_client)
    long_field = _text_field(auth_client, "Old long field")
    other_field = _text_field(auth_client, "Other field")

    # Seed the row through the API (short), then plant the over-cap value.
    assert _set(
        auth_client, member, [{"field_id": long_field, "value": "seed"}]
    ).status_code == 200
    planted = "y" * (CAP + 500)
    _plant_oversized_value(long_field, member, planted)

    resp = _set(
        auth_client,
        member,
        [
            {"field_id": long_field, "value": planted},
            {"field_id": other_field, "value": "a normal answer"},
        ],
    )
    assert resp.status_code == 200, resp.text

    values = {v["field_id"]: v["value"] for v in resp.json()}
    assert values[long_field] == planted
    assert values[other_field] == "a normal answer"


def test_changing_an_existing_oversized_value_is_rejected(
    auth_client: httpx.Client,
):
    """"Unchanged" means unchanged. Editing an over-cap value into different
    over-cap text is new text, and new text obeys the cap."""
    member = _member(auth_client)
    field = _text_field(auth_client, "Still long")

    assert _set(
        auth_client, member, [{"field_id": field, "value": "seed"}]
    ).status_code == 200
    planted = "z" * (CAP + 500)
    _plant_oversized_value(field, member, planted)

    resp = _set(auth_client, member, [{"field_id": field, "value": planted + "!"}])
    assert resp.status_code == 400, resp.text

    # And the stored value is untouched by the refusal.
    current = auth_client.get(f"/v1/members/{member}/fields").json()
    assert {v["field_id"]: v["value"] for v in current}[field] == planted
