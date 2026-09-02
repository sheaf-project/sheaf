"""Tests for System Safety: grace periods, deferred safety changes, finalization."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest


def _set_system_safety_via_db(user_email: str, **fields) -> None:
    """Directly patch columns on the user's System row (bypasses re-auth + loosening delay)."""
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


def _backdate_pending_action(pending_id: str, days: int) -> None:
    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.pending_action import PendingAction

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            pending = await db.get(PendingAction, uuid.UUID(pending_id))
            assert pending is not None
            pending.finalize_after = datetime.now(UTC) - timedelta(days=days)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _backdate_safety_change(change_id: str, days: int) -> None:
    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.safety_change_request import SafetyChangeRequest

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            change = await db.get(SafetyChangeRequest, uuid.UUID(change_id))
            assert change is not None
            change.finalize_after = datetime.now(UTC) - timedelta(days=days)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _register(client: httpx.Client) -> tuple[str, str]:
    email = f"safety-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return email, "testpassword123"


# ---------------------------------------------------------------------------
# Baseline: safety off — delete is immediate, nothing queued
# ---------------------------------------------------------------------------


def test_delete_immediate_when_safety_off(auth_client: httpx.Client):
    member = auth_client.post("/v1/members", json={"name": "Alpha"}).json()
    resp = auth_client.delete(f"/v1/members/{member['id']}")
    assert resp.status_code == 204

    listing = auth_client.get("/v1/system/safety").json()
    assert listing["pending_actions"] == []


# ---------------------------------------------------------------------------
# Delete queues when safeguarded
# ---------------------------------------------------------------------------


def test_delete_queues_when_safety_on(client: httpx.Client):
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
    )
    member = client.post("/v1/members", json={"name": "Beta"}).json()

    resp = client.delete(f"/v1/members/{member['id']}")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "pending_action_id" in body
    assert "finalize_after" in body

    # Member still exists
    assert client.get(f"/v1/members/{member['id']}").status_code == 200

    # Shows up in pending list
    listing = client.get("/v1/system/safety").json()
    assert len(listing["pending_actions"]) == 1
    assert listing["pending_actions"][0]["target_label"] == "Beta"


def _read_pending_row_raw(pending_id: str) -> tuple[str, str]:
    """Return the raw at-rest (target_label, fronting_member_names) strings
    straight from the DB, bypassing the API's decrypt-on-read."""
    result: dict = {}

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.pending_action import PendingAction

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            pending = await db.get(PendingAction, uuid.UUID(pending_id))
            assert pending is not None
            result["target_label"] = pending.target_label
            result["fronting_member_names"] = pending.fronting_member_names
        await engine.dispose()

    asyncio.run(_run())
    return result["target_label"], result["fronting_member_names"]


def test_pending_action_content_encrypted_at_rest(client: httpx.Client):
    """target_label and fronting_member_names are ciphertext in the DB, but the
    read endpoint returns the plaintext."""
    import json

    from sheaf.crypto import decrypt
    from sheaf.encrypted_fields import (
        pending_fronting_names_aad,
        pending_target_label_aad,
    )

    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
        safety_applies_to_fronts=True,
    )
    alice = client.post("/v1/members", json={"name": "Alice"}).json()
    victim = client.post("/v1/members", json={"name": "Victim Member"}).json()

    # Alice is fronting when the destructive action is queued.
    client.post("/v1/fronts", json={"member_ids": [alice["id"]]})

    resp = client.delete(f"/v1/members/{victim['id']}")
    assert resp.status_code == 202, resp.text
    pending_id = resp.json()["pending_action_id"]

    # At rest: neither column contains the plaintext, and both decrypt back.
    raw_label, raw_names = _read_pending_row_raw(pending_id)
    assert raw_label != "Victim Member"
    assert "Victim Member" not in raw_label
    assert (
        decrypt(raw_label, aad=pending_target_label_aad(pending_id))
        == "Victim Member"
    )

    assert raw_names != "Alice"
    assert "Alice" not in raw_names
    assert json.loads(
        decrypt(raw_names, aad=pending_fronting_names_aad(pending_id))
    ) == ["Alice"]

    # Read endpoint returns the decrypted plaintext.
    pending = client.get("/v1/system/safety").json()["pending_actions"][0]
    assert pending["target_label"] == "Victim Member"
    assert pending["fronting_member_names"] == ["Alice"]


def test_delete_queues_captures_fronting_snapshot(client: httpx.Client):
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
        safety_applies_to_fronts=True,
    )
    alice = client.post("/v1/members", json={"name": "Alice"}).json()
    bob = client.post("/v1/members", json={"name": "Bob"}).json()
    charlie = client.post("/v1/members", json={"name": "Charlie"}).json()

    # Start a front with Alice + Bob
    client.post("/v1/fronts", json={"member_ids": [alice["id"], bob["id"]]})

    # Queue Charlie's deletion
    resp = client.delete(f"/v1/members/{charlie['id']}")
    assert resp.status_code == 202

    listing = client.get("/v1/system/safety").json()
    pending = listing["pending_actions"][0]
    assert set(pending["fronting_member_names"]) == {"Alice", "Bob"}


# ---------------------------------------------------------------------------
# Category toggles: only enabled categories are safeguarded
# ---------------------------------------------------------------------------


def test_category_toggle_controls_safeguarding(client: httpx.Client):
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
        safety_applies_to_tags=False,
    )
    member = client.post("/v1/members", json={"name": "Zed"}).json()
    tag = client.post("/v1/tags", json={"name": "TestTag"}).json()

    # Members is enabled — should queue
    assert client.delete(f"/v1/members/{member['id']}").status_code == 202

    # Tags is not enabled — immediate delete
    assert client.delete(f"/v1/tags/{tag['id']}").status_code == 204


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_pending_action(client: httpx.Client):
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
    )
    member = client.post("/v1/members", json={"name": "Delta"}).json()
    resp = client.delete(f"/v1/members/{member['id']}")
    pending_id = resp.json()["pending_action_id"]

    cancel = client.delete(f"/v1/system/safety/pending-actions/{pending_id}")
    assert cancel.status_code == 204

    # Member still exists
    assert client.get(f"/v1/members/{member['id']}").status_code == 200
    # No longer pending
    listing = client.get("/v1/system/safety").json()
    assert listing["pending_actions"] == []


# ---------------------------------------------------------------------------
# One queued action per target
# ---------------------------------------------------------------------------


def _pending_of_type(client: httpx.Client, action_type: str) -> list[dict]:
    listing = client.get("/v1/system/safety").json()
    return [
        p for p in listing["pending_actions"] if p["action_type"] == action_type
    ]


# One entity per safeguarded surface that has its own list page, since that is
# where a duplicate row shows up. The path is both the create and the delete
# collection - they match everywhere here.
_DOUBLE_QUEUE_CASES = [
    pytest.param(
        "safety_applies_to_members",
        "/v1/members",
        {"name": "Twice-deleted"},
        "member_delete",
        id="members",
    ),
    pytest.param(
        "safety_applies_to_groups",
        "/v1/groups",
        {"name": "Twice-deleted group"},
        "group_delete",
        id="groups",
    ),
    pytest.param(
        "safety_applies_to_fields",
        "/v1/fields",
        {"name": "Twice-deleted field", "field_type": "text"},
        "field_delete",
        id="fields",
    ),
    pytest.param(
        "safety_applies_to_tags",
        "/v1/tags",
        {"name": "Twice-deleted tag"},
        "tag_delete",
        id="tags",
    ),
    pytest.param(
        "safety_applies_to_relationships",
        "/v1/relationship-types",
        {
            "name": "Twice-deleted type",
            "symmetry": "symmetric",
            "forward_label": "twin",
        },
        "relationship_type_delete",
        id="relationship-types",
    ),
]


@pytest.mark.parametrize(
    "safety_field,path,create_body,action_type", _DOUBLE_QUEUE_CASES
)
def test_second_delete_while_queued_is_a_conflict(
    client: httpx.Client,
    safety_field: str,
    path: str,
    create_body: dict,
    action_type: str,
):
    """A second delete on an already-queued target is a 409, not a second row.

    The duplicate row was the visible bug: two entries on the Safety page for
    one thing, where cancelling either left the target still on its way out.
    Cancelling has to leave the target queueable again, so this walks the
    whole loop - queue, refuse, cancel, queue afresh.
    """
    email, _ = _register(client)
    _set_system_safety_via_db(
        email, safety_grace_period_days=7, **{safety_field: True}
    )

    created = client.post(path, json=create_body)
    assert created.status_code == 201, created.text
    target_id = created.json()["id"]

    first = client.delete(f"{path}/{target_id}")
    assert first.status_code == 202, first.text
    first_pending = first.json()["pending_action_id"]

    second = client.delete(f"{path}/{target_id}")
    assert second.status_code == 409, second.text
    assert "already queued" in second.json()["detail"]

    # Exactly one row, and it is the one the first delete created.
    assert [p["id"] for p in _pending_of_type(client, action_type)] == [
        first_pending
    ]

    cancel = client.delete(f"/v1/system/safety/pending-actions/{first_pending}")
    assert cancel.status_code == 204, cancel.text
    assert _pending_of_type(client, action_type) == []

    # Cancelled means cancelled: the target can be queued again, and gets a
    # fresh row rather than reviving the old one.
    third = client.delete(f"{path}/{target_id}")
    assert third.status_code == 202, third.text
    assert third.json()["pending_action_id"] != first_pending
    assert len(_pending_of_type(client, action_type)) == 1


# ---------------------------------------------------------------------------
# Finalization via admin job trigger
# ---------------------------------------------------------------------------


def test_finalize_pending_action_runs(
    client: httpx.Client, admin_client: httpx.Client
):
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
    )
    member = client.post("/v1/members", json={"name": "Epsilon"}).json()
    pending_id = client.delete(f"/v1/members/{member['id']}").json()[
        "pending_action_id"
    ]

    _backdate_pending_action(pending_id, days=8)

    resp = admin_client.post("/v1/admin/jobs/finalize_pending_actions/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"

    # Member is gone
    assert client.get(f"/v1/members/{member['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Asymmetric loosening delay
# ---------------------------------------------------------------------------


def test_tightening_applies_immediately(client: httpx.Client):
    _register(client)
    resp = client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 7, "applies_to_members": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["applied"]) == {"grace_period_days", "applies_to_members"}
    assert body["deferred"] == []
    assert body["settings"]["grace_period_days"] == 7


def test_loosening_is_deferred(client: httpx.Client):
    email, password = _register(client)
    # Tighten first (immediate).
    client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "password",
        },
    )

    # Attempt to loosen (lower grace) — needs re-auth and should defer.
    resp = client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 1, "password": password},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deferred"] == ["grace_period_days"]
    assert body["settings"]["grace_period_days"] == 7  # unchanged
    assert body["pending_change"] is not None


def test_loosening_requires_reauth(client: httpx.Client):
    _register(client)
    client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "password",
        },
    )

    # No password supplied — should be rejected (400 = client didn't
    # send the field; 401 is reserved for "wrong password").
    resp = client.patch("/v1/system/safety", json={"grace_period_days": 0})
    assert resp.status_code == 400


def test_cancel_pending_safety_change(client: httpx.Client):
    _, password = _register(client)
    client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "password",
        },
    )
    resp = client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 0, "password": password},
    )
    change_id = resp.json()["pending_change"]["id"]

    cancel = client.delete(f"/v1/system/safety/pending-changes/{change_id}")
    assert cancel.status_code == 204

    current = client.get("/v1/system/safety").json()
    assert current["settings"]["grace_period_days"] == 7
    assert current["pending_changes"] == []


def test_finalize_safety_change_applies_loosening(
    client: httpx.Client, admin_client: httpx.Client
):
    _, password = _register(client)
    client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "password",
        },
    )
    resp = client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 1, "password": password},
    )
    change_id = resp.json()["pending_change"]["id"]

    _backdate_safety_change(change_id, days=8)

    run = admin_client.post("/v1/admin/jobs/finalize_safety_changes/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "success"

    current = client.get("/v1/system/safety").json()
    assert current["settings"]["grace_period_days"] == 1


def test_mixed_change_splits_applied_and_deferred(client: httpx.Client):
    _, password = _register(client)
    client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "password",
        },
    )
    # Raise grace (tighten) + drop auth_tier to none (loosen) in one call.
    resp = client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 14,
            "auth_tier": "none",
            "password": password,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "grace_period_days" in body["applied"]
    assert "auth_tier" in body["deferred"]
    # Tightening visible now.
    assert body["settings"]["grace_period_days"] == 14
    # Loosening still pending.
    assert body["settings"]["auth_tier"] == "password"


def test_loosening_when_grace_zero_applies_immediately(client: httpx.Client):
    """If safety is fully off (grace=0), nothing to defer — loosening applies instantly."""
    _register(client)
    # Safety starts off (grace=0). Lowering auth tier or toggling off categories
    # should apply immediately since there's no grace to wait through.
    resp = client.patch(
        "/v1/system/safety",
        json={"applies_to_members": False},
    )
    assert resp.status_code == 200
    # Already False — nothing applied, nothing deferred.
    assert resp.json()["applied"] == []
    assert resp.json()["deferred"] == []


# ---------------------------------------------------------------------------
# Re-auth gate on destructive endpoints (delete_confirmation tier)
# ---------------------------------------------------------------------------


def test_delete_endpoint_requires_password_when_tier_set(client: httpx.Client):
    """delete_confirmation=password gates the delete endpoint regardless of grace."""
    email, password = _register(client)
    _set_system_safety_via_db(email, delete_confirmation="password")
    member = client.post("/v1/members", json={"name": "Gated"}).json()

    # No body → 400 Password required (missing input)
    no_body = client.delete(f"/v1/members/{member['id']}")
    assert no_body.status_code == 400
    assert no_body.json()["detail"] == "Password required"

    # Wrong password → 403 Incorrect password. 403 (not 401) because the
    # caller IS authenticated; the step-up password gate is denying this
    # specific destructive action. 401 would trip the frontend's silent
    # token-refresh-and-retry path, which can't fix a wrong password.
    wrong = client.request(
        "DELETE",
        f"/v1/members/{member['id']}",
        json={"password": "not-the-password"},
    )
    assert wrong.status_code == 403
    assert wrong.json()["detail"] == "Incorrect password"

    # Member still exists.
    assert client.get(f"/v1/members/{member['id']}").status_code == 200

    # Correct password → 204 (no safety grace configured)
    ok = client.request(
        "DELETE",
        f"/v1/members/{member['id']}",
        json={"password": password},
    )
    assert ok.status_code == 204


def test_archive_requires_password_when_category_on(client: httpx.Client):
    """The `archive` category gates the archive endpoint with re-auth (no grace)."""
    email, password = _register(client)
    _set_system_safety_via_db(
        email, delete_confirmation="password", safety_applies_to_archive=True
    )
    mid = client.post("/v1/members", json={"name": "GatedArchive"}).json()["id"]

    # No password -> rejected; member stays active.
    resp = client.post(f"/v1/members/{mid}/archive")
    assert resp.status_code == 400, resp.text
    assert client.get(f"/v1/members/{mid}").json()["archived_at"] is None

    # Correct password -> archived.
    resp = client.post(f"/v1/members/{mid}/archive", json={"password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived_at"] is not None

    # Unarchive stays ungated even with the category on.
    resp = client.post(f"/v1/members/{mid}/unarchive")
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived_at"] is None


def test_archive_ungated_when_category_off(client: httpx.Client):
    """A configured auth tier alone does NOT gate archiving; only the
    per-category `archive` toggle does (unlike delete, gated by tier)."""
    email, _password = _register(client)
    _set_system_safety_via_db(email, delete_confirmation="password")
    mid = client.post("/v1/members", json={"name": "UngatedArchive"}).json()["id"]

    resp = client.post(f"/v1/members/{mid}/archive")
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived_at"] is not None


def test_delete_endpoint_reauth_then_queues_when_safeguarded(client: httpx.Client):
    """Re-auth must pass first, *then* the safeguard queues — both gates apply."""
    email, password = _register(client)
    _set_system_safety_via_db(
        email,
        delete_confirmation="password",
        safety_grace_period_days=7,
        safety_applies_to_members=True,
    )
    member = client.post("/v1/members", json={"name": "DoubleGated"}).json()

    # Wrong password is rejected before the safeguard even considers queuing.
    wrong = client.request(
        "DELETE",
        f"/v1/members/{member['id']}",
        json={"password": "nope"},
    )
    assert wrong.status_code == 403
    assert client.get("/v1/system/safety").json()["pending_actions"] == []

    # Correct password → 202 pending (safeguard takes over).
    ok = client.request(
        "DELETE",
        f"/v1/members/{member['id']}",
        json={"password": password},
    )
    assert ok.status_code == 202
    assert client.get(f"/v1/members/{member['id']}").status_code == 200
    assert len(client.get("/v1/system/safety").json()["pending_actions"]) == 1


# ---------------------------------------------------------------------------
# pending_delete_at on list/get responses
# ---------------------------------------------------------------------------


def test_member_lists_surface_pending_delete_at(client: httpx.Client):
    """A queued member-delete shows up as pending_delete_at on the member's
    list + GET responses so the UI can flag it in the listing."""
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
    )
    member = client.post("/v1/members", json={"name": "Doomed"}).json()
    assert (
        client.delete(f"/v1/members/{member['id']}").status_code == 202
    )

    listed = client.get("/v1/members").json()
    doomed = next(m for m in listed if m["id"] == member["id"])
    assert doomed["pending_delete_at"] is not None

    single = client.get(f"/v1/members/{member['id']}").json()
    assert single["pending_delete_at"] is not None
    # Same timestamp from both endpoints.
    assert single["pending_delete_at"] == doomed["pending_delete_at"]


def test_member_pending_delete_at_null_when_not_queued(client: httpx.Client):
    """No queued action -> pending_delete_at is null on every response."""
    _register(client)
    member = client.post("/v1/members", json={"name": "Safe"}).json()
    assert member["pending_delete_at"] is None
    assert client.get(f"/v1/members/{member['id']}").json()["pending_delete_at"] is None
    listed = client.get("/v1/members").json()
    assert next(m for m in listed if m["id"] == member["id"])["pending_delete_at"] is None


# ---------------------------------------------------------------------------
# Retention-cap loosening: 0 = unlimited (pure unit test of split_safety_changes)
# ---------------------------------------------------------------------------


def test_split_safety_treats_zero_retention_cap_as_unlimited():
    """0 = unlimited for a revision-retention cap, exactly like None.

    Moving from unlimited (0 or None) to a finite cap is the data-destroying
    direction - it turns "keep everything" into "delete all but the newest N" -
    so it must take the deferred/guarded path (grace + re-auth), never apply
    silently.

    Regression guard: the prior code treated a stored 0 as the literal integer
    zero (the smallest cap), so a 0 -> 5 change passed `5 < 0` == False and was
    applied immediately with no grace and no re-auth.
    """
    from sheaf.models.system import System
    from sheaf.services.system_safety import split_safety_changes

    # current = 0 (unlimited) -> finite cap 5: destructive, must defer.
    split = split_safety_changes(
        System(journal_max_revisions=0), {"journal_max_revisions": 5}
    )
    assert split.deferred == {"journal_max_revisions": 5}
    assert split.applied == {}

    # current = None (use tier default) -> finite cap 5: same, must defer.
    split_none = split_safety_changes(
        System(journal_max_revisions=None), {"journal_max_revisions": 5}
    )
    assert split_none.deferred == {"journal_max_revisions": 5}

    # Safe direction: finite cap 5 -> 0 (unlimited) keeps more, applies now.
    split_loosen = split_safety_changes(
        System(journal_max_revisions=5), {"journal_max_revisions": 0}
    )
    assert split_loosen.applied == {"journal_max_revisions": 0}
    assert split_loosen.deferred == {}

    # Tightening between two finite caps still defers (10 -> 5).
    split_tighten = split_safety_changes(
        System(journal_max_revisions=10), {"journal_max_revisions": 5}
    )
    assert split_tighten.deferred == {"journal_max_revisions": 5}
    assert split_tighten.applied == {}


# ---------------------------------------------------------------------------
# Pending exposures: staged flip-to-public raises surface for the banner
# ---------------------------------------------------------------------------


def _stage_member_view_exposure(
    email: str, member_id: str, activates_at: datetime
) -> None:
    """Leave behind the shape a member privacy raise creates: a share view with
    the member's row demoted to PENDING (see update_member)."""
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.share import ShareItemStatus, ShareView, ShareViewMember
        from sheaf.models.system import System
        from sheaf.models.user import User

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            email_hash = blind_index(email)
            user = (
                await db.execute(select(User).where(User.email_hash == email_hash))
            ).scalar_one()
            system = (
                await db.execute(select(System).where(System.user_id == user.id))
            ).scalar_one()
            view = ShareView(system_id=system.id, name=f"view-{uuid.uuid4().hex[:8]}")
            db.add(view)
            await db.flush()
            db.add(
                ShareViewMember(
                    view_id=view.id,
                    member_id=uuid.UUID(member_id),
                    status=ShareItemStatus.PENDING.value,
                    activates_at=activates_at,
                    created_at=datetime.now(UTC),
                )
            )
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _stage_pending_grant(email: str, activates_at: datetime) -> None:
    """Leave behind the shape a first publish creates under a grace window: a
    view with a grant held PENDING until its activation time."""
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.share import (
            ShareGrant,
            ShareGrantStatus,
            ShareSubjectType,
            ShareView,
        )
        from sheaf.models.system import System
        from sheaf.models.user import User

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            email_hash = blind_index(email)
            user = (
                await db.execute(select(User).where(User.email_hash == email_hash))
            ).scalar_one()
            system = (
                await db.execute(select(System).where(System.user_id == user.id))
            ).scalar_one()
            view = ShareView(system_id=system.id, name=f"view-{uuid.uuid4().hex[:8]}")
            db.add(view)
            await db.flush()
            db.add(
                ShareGrant(
                    system_id=system.id,
                    view_id=view.id,
                    subject_type=ShareSubjectType.LINK.value,
                    token_hash=uuid.uuid4().hex,
                    status=ShareGrantStatus.PENDING.value,
                    activates_at=activates_at,
                    created_by_user_id=user.id,
                    created_at=datetime.now(UTC),
                )
            )
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def test_pending_exposures_includes_a_staged_grant(client: httpx.Client):
    """A first publish behind a grace window is the exposure the banner most
    needs to catch, so a pending grant must appear."""
    email, _ = _register(client)
    grant_at = datetime.now(UTC) + timedelta(days=2)
    _stage_pending_grant(email, grant_at)

    exposures = client.get("/v1/system/safety").json()["pending_exposures"]
    by_kind = {e["kind"]: e["activates_at"] for e in exposures}
    assert "share_grant" in by_kind
    assert abs(
        datetime.fromisoformat(by_kind["share_grant"]) - grant_at
    ) < timedelta(seconds=2)


def test_pending_exposures_empty_when_nothing_staged(client: httpx.Client):
    _register(client)
    client.post("/v1/members", json={"name": "Quiet"})
    listing = client.get("/v1/system/safety").json()
    assert listing["pending_exposures"] == []


def test_pending_exposures_lists_system_and_member_raises(client: httpx.Client):
    from sheaf.models.system import PrivacyLevel

    email, _ = _register(client)
    member = client.post("/v1/members", json={"name": "Rising"}).json()

    system_at = datetime.now(UTC) + timedelta(days=5)
    member_at = datetime.now(UTC) + timedelta(days=3)

    # A staged master-switch raise: pending_privacy set with its activation time.
    _set_system_safety_via_db(
        email,
        pending_privacy=PrivacyLevel.PUBLIC,
        privacy_activates_at=system_at,
    )
    # A staged member raise: the member's share-view row is held PENDING.
    _stage_member_view_exposure(email, member["id"], member_at)

    listing = client.get("/v1/system/safety").json()
    exposures = listing["pending_exposures"]

    by_kind = {e["kind"]: e["activates_at"] for e in exposures}
    assert set(by_kind) == {"system_privacy", "member_privacy"}
    assert abs(
        datetime.fromisoformat(by_kind["system_privacy"]) - system_at
    ) < timedelta(seconds=2)
    assert abs(
        datetime.fromisoformat(by_kind["member_privacy"]) - member_at
    ) < timedelta(seconds=2)
