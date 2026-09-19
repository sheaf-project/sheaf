"""What the admin safety-reset does, and does not, do to already-queued work.

Needs the docker stack, and the `public_profiles` config: the staged-exposure
half of this only exists when the sharing surface is on.

`reset-safety` is the break-glass lever for an owner who has locked themselves
out with their own safeguards, and the question these tests pin down is what it
leaves behind. Three kinds of thing can already be in flight when support
presses it, and they behave differently:

  - queued deletions (`pending_actions`), which it documents leaving alone,
  - a queued LOOSENING of the safety settings (`safety_change_requests`),
  - staged exposures: a grant, member, field or flag parked behind the
    visibility grace window, which no admin lever touches at all.

These are characterisation tests. They assert what the code does today so the
behaviour is on the record before anyone argues about what it should do; where
today's answer looks wrong, the test says so in its docstring rather than
pretending otherwise.

The queued-deletion case is the exception: leaving those alone is a decided
position rather than an open question, so its test is written as a decision to
hold rather than a finding to act on.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8001")

pytestmark = pytest.mark.public_profiles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _in_db(work) -> None:
    """Run `work(db)` straight against the test database, then commit."""

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            await work(db)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _me(c: httpx.Client) -> str:
    return c.get("/v1/auth/me").json()["id"]


def _member(c: httpx.Client, name: str | None = None) -> str:
    r = c.post(
        "/v1/members",
        json={"name": name or f"RS-{uuid.uuid4().hex[:6]}", "privacy": "public"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _view(c: httpx.Client, members: list[str]) -> str:
    r = c.post(
        "/v1/share-views",
        json={"name": f"RS-{uuid.uuid4().hex[:6]}", "include_members": True},
    )
    assert r.status_code == 201, r.text
    vid = r.json()["id"]
    for m in members:
        added = c.post(f"/v1/share-views/{vid}/members", json={"member_id": m})
        assert added.status_code == 200, added.text
    return vid


def _go_public(c: httpx.Client) -> None:
    """System privacy is the master ceiling over the public surface, so a
    system has to be public before it can publish anything at all. Called
    before safety is armed, so the raise itself is instant."""
    r = c.patch("/v1/systems/me", json={"privacy": "public"})
    assert r.status_code == 200, r.text


def _arm(c: httpx.Client, days: int = 7) -> None:
    """Grace window + password tier on the profile_visibility category."""
    r = c.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": days,
            "applies_to_profile_visibility": True,
            "auth_tier": "password",
        },
    )
    assert r.status_code == 200, r.text


def _safety(c: httpx.Client) -> dict:
    r = c.get("/v1/system/safety")
    assert r.status_code == 200, r.text
    return r.json()


def _reset(admin: httpx.Client, user_id: str, reason: str = "audit") -> dict:
    r = admin.post(
        f"/v1/admin/users/{user_id}/reset-safety", json={"reason": reason}
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Which categories the reset actually clears
# ---------------------------------------------------------------------------


def test_reset_leaves_three_categories_armed(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """`_SAFETY_TOGGLE_FIELDS` lists twelve of the sixteen categories.

    relationships, archive and profile_visibility are not in it, so a reset
    that advertises itself as clearing "all System Safety toggles" leaves
    three of them exactly as they were. profile_visibility is the one that
    bites: it is the only category that defaults ON, and it is the one an
    owner who cannot publish is complaining about.
    """
    uid = _me(auth_client)
    armed = auth_client.patch(
        "/v1/system/safety",
        json={
            "applies_to_members": True,
            "applies_to_relationships": True,
            "applies_to_archive": True,
            "applies_to_profile_visibility": True,
        },
    )
    assert armed.status_code == 200, armed.text

    changed = _reset(admin_client, uid)["changed_fields"]
    assert "safety_applies_to_members" in changed
    assert "safety_applies_to_relationships" not in changed
    assert "safety_applies_to_archive" not in changed
    assert "safety_applies_to_profile_visibility" not in changed

    after = _safety(auth_client)["settings"]
    assert after["applies_to_members"] is False
    assert after["applies_to_relationships"] is True
    assert after["applies_to_archive"] is True
    assert after["applies_to_profile_visibility"] is True


# ---------------------------------------------------------------------------
# Queued deletions: left queued on purpose
# ---------------------------------------------------------------------------


def test_reset_leaves_a_queued_delete_queued_and_a_redelete_is_instant(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """The decided behaviour, not an oversight: the reset does not drain the
    deletion queue, and it does not have to.

    Anything the owner wanted gone badly enough to be the reason for the
    support request is one cancel and one re-delete away, because the reset
    zeroes the grace period and `is_safeguarded` short-circuits on
    `grace <= 0` before it ever reads a category toggle. So the re-delete
    lands immediately whatever the category flags say, which is also why the
    three toggles the reset misses do not bite here. Cancelling is instant
    and ungated by design.

    The alternative - draining the queue as part of the reset - would finish
    deletions the owner is still inside the window for, which is the one thing
    the window exists to prevent. `bypass-pending` stays the lever for an
    owner who does want the queue through now.
    """
    uid = _me(auth_client)
    mid = _member(auth_client)
    armed = auth_client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 7, "applies_to_members": True},
    )
    assert armed.status_code == 200, armed.text

    queued = auth_client.delete(f"/v1/members/{mid}")
    assert queued.status_code in (200, 202), queued.text
    actions = _safety(auth_client)["pending_actions"]
    assert len(actions) == 1, actions
    action_id = actions[0]["id"]

    _reset(admin_client, uid)

    # Still queued, still cancellable: the reset did not touch it.
    after = _safety(auth_client)["pending_actions"]
    assert len(after) == 1, after
    assert after[0]["id"] == action_id

    cancelled = auth_client.delete(f"/v1/system/safety/pending-actions/{action_id}")
    assert cancelled.status_code == 204, cancelled.text
    assert _safety(auth_client)["pending_actions"] == []
    assert auth_client.get(f"/v1/members/{mid}").status_code == 200

    # With grace at 0 the re-delete is immediate rather than queued again.
    redone = auth_client.delete(f"/v1/members/{mid}")
    assert redone.status_code in (200, 204), redone.text
    assert _safety(auth_client)["pending_actions"] == []
    assert auth_client.get(f"/v1/members/{mid}").status_code == 404


# ---------------------------------------------------------------------------
# A queued loosening outlives the reset and reverses it
# ---------------------------------------------------------------------------


def test_queued_loosening_reapplies_itself_after_a_reset(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """The reset is undone later by a change the owner requested before it.

    Reducing the grace period is a loosening, so it parks in a
    `SafetyChangeRequest` and waits out the window it is trying to shorten.
    If support resets the account while that request is still queued, the
    reset zeroes the grace period now - and then the sweep fires, applies the
    stored `{grace_period_days: 3}` verbatim, and the account has a grace
    period again with nothing in the admin audit log to explain it.
    """
    uid = _me(auth_client)
    _arm(auth_client, days=7)

    queued = auth_client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 3, "password": "testpassword123"},
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["deferred"] == ["grace_period_days"]

    assert _reset(admin_client, uid)["changed_fields"]
    assert _safety(auth_client)["settings"]["grace_period_days"] == 0

    # The queued request is still pending: the reset never looked at it.
    pending_changes = _safety(auth_client)["pending_changes"]
    assert len(pending_changes) == 1, pending_changes

    _backdate_safety_change(uid)
    run = admin_client.post("/v1/admin/jobs/finalize_safety_changes/run")
    assert run.status_code == 200, run.text

    assert _safety(auth_client)["settings"]["grace_period_days"] == 3


def _backdate_safety_change(user_id: str) -> None:
    async def _work(db) -> None:
        from sqlalchemy import select

        from sheaf.models.safety_change_request import (
            SafetyChangeRequest,
            SafetyChangeStatus,
        )
        from sheaf.models.system import System

        system = (
            await db.execute(
                select(System).where(System.user_id == uuid.UUID(user_id))
            )
        ).scalar_one()
        rows = (
            await db.execute(
                select(SafetyChangeRequest).where(
                    SafetyChangeRequest.system_id == system.id,
                    SafetyChangeRequest.status == SafetyChangeStatus.PENDING,
                )
            )
        ).scalars().all()
        assert rows, "expected a queued safety change"
        for row in rows:
            row.finalize_after = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)


# ---------------------------------------------------------------------------
# Staged exposures: no admin lever reaches them
# ---------------------------------------------------------------------------


def test_reset_does_not_cancel_a_staged_publish(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """A grant parked behind the grace window still goes live after a reset.

    Defensible as the default (the owner asked to publish and the reset was
    about their settings, not their intent), but it is not what "reset safety"
    reads like, and the endpoint's own docstring only mentions pending_actions.
    """
    uid = _me(auth_client)
    _go_public(auth_client)
    _arm(auth_client, days=7)
    vid = _view(auth_client, [_member(auth_client)])

    attest = auth_client.post("/v1/auth/me/attest-adult")
    assert attest.status_code == 200, attest.text
    granted = auth_client.post(
        "/v1/share-grants",
        json={
            "view_id": vid,
            "subject_type": "public",
            "password": "testpassword123",
        },
    )
    assert granted.status_code == 201, granted.text
    grant = granted.json()["grant"]
    assert grant["status"] == "pending", granted.text
    assert grant["activates_at"] is not None

    assert len(_safety(auth_client)["pending_exposures"]) >= 1

    _reset(admin_client, uid)

    # Still staged, still scheduled, unchanged by the reset.
    still = _safety(auth_client)["pending_exposures"]
    assert len(still) >= 1, still
    _backdate_grant(grant["id"])
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    audit = auth_client.get("/v1/sharing/audit").json()
    live = [e for e in audit["entries"] if e["view_id"] == vid]
    assert live and live[0]["grant"]["status"] == "active", audit


def test_bypass_pending_does_not_reach_a_staged_publish(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """The "push it through now" lever cannot push a publish through.

    `bypass-pending` drains `pending_actions`, and every member of
    `PendingActionType` is a delete, an unpin or a revoke. A staged exposure
    lives on the share tables instead, so support has no way to finish one
    early - the owner waits out the window or revokes and re-grants.
    """
    uid = _me(auth_client)
    _go_public(auth_client)
    _arm(auth_client, days=7)
    vid = _view(auth_client, [_member(auth_client)])

    attest = auth_client.post("/v1/auth/me/attest-adult")
    assert attest.status_code == 200, attest.text
    granted = auth_client.post(
        "/v1/share-grants",
        json={
            "view_id": vid,
            "subject_type": "public",
            "password": "testpassword123",
        },
    )
    assert granted.status_code == 201, granted.text
    assert granted.json()["grant"]["status"] == "pending"

    drained = admin_client.post(
        f"/v1/admin/users/{uid}/bypass-pending", json={"reason": "audit"}
    )
    assert drained.status_code == 200, drained.text
    assert drained.json()["finalized_count"] == 0

    assert len(_safety(auth_client)["pending_exposures"]) >= 1


def _backdate_grant(grant_id: str) -> None:
    async def _work(db) -> None:
        from sheaf.models.share import ShareGrant

        grant = await db.get(ShareGrant, uuid.UUID(grant_id))
        assert grant is not None
        assert grant.activates_at is not None
        grant.activates_at = datetime.now(UTC) - timedelta(minutes=1)

    _in_db(_work)
