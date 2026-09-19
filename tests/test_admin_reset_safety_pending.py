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


def test_reset_covers_every_category_column():
    """The reset's field list is derived, so it cannot drift again.

    The hand-written tuple it replaced held twelve of the fifteen
    `safety_applies_to_*` columns: relationships, archive and
    profile_visibility were added later and nobody came back to this file, so
    a reset advertising itself as clearing "all System Safety toggles" left
    three armed. This asserts the derived set is the column set, which is the
    property that stops the next category from being forgotten.

    In-process rather than over HTTP: it is a claim about the model, and the
    stack runs the server in a separate process.
    """
    from sheaf.api.v1.admin_emergency import _safety_category_defaults
    from sheaf.models.system import System

    columns = {
        c.name
        for c in System.__table__.columns
        if c.name.startswith("safety_applies_to_")
    }
    assert _safety_category_defaults().keys() == columns
    assert len(columns) >= 15, columns


def test_reset_returns_every_category_to_its_declared_default(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """Reset means "how a new account starts", not "everything off".

    Fourteen categories gate a destructive action and default off, so they
    come back off. profile_visibility gates an EXPOSING one and defaults ON,
    so it comes back ON: a support ticket about being unable to delete
    something is not a reason to disarm the guard on publishing. That costs
    the owner nothing, because the same reset puts the grace period at 0 and
    the tier at NONE, so the re-armed category's step-up verifies nothing -
    which the next test pins directly.
    """
    uid = _me(auth_client)
    armed = auth_client.patch(
        "/v1/system/safety",
        json={
            "applies_to_members": True,
            "applies_to_relationships": True,
            "applies_to_archive": True,
        },
    )
    assert armed.status_code == 200, armed.text
    # Turning the exposure guard off is a loosening, but with no grace period
    # in force it applies immediately, so this lands before the reset.
    off = auth_client.patch(
        "/v1/system/safety", json={"applies_to_profile_visibility": False}
    )
    assert off.status_code == 200, off.text
    assert _safety(auth_client)["settings"]["applies_to_profile_visibility"] is False

    changed = _reset(admin_client, uid)["changed_fields"]
    for field in (
        "safety_applies_to_members",
        "safety_applies_to_relationships",
        "safety_applies_to_archive",
        "safety_applies_to_profile_visibility",
    ):
        assert field in changed, changed

    after = _safety(auth_client)["settings"]
    assert after["applies_to_members"] is False
    assert after["applies_to_relationships"] is False
    assert after["applies_to_archive"] is False
    assert after["applies_to_profile_visibility"] is True


def test_reset_leaves_the_exposure_guard_instantly_disableable(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """The reset hands back a working off switch, not a re-armed trap.

    This is the whole reason re-arming profile_visibility is safe. The reset
    zeroes the grace period, and `update_system_safety` applies a loosening
    immediately whenever `safety_grace_period_days <= 0` instead of parking it
    in a SafetyChangeRequest. So an owner who does not want the exposure check
    turns it off in one request that takes effect at once, rather than waiting
    out a window to be allowed to shorten a window - which is the trap that
    sent them to support in the first place.
    """
    uid = _me(auth_client)
    _arm(auth_client, days=7)
    _reset(admin_client, uid)

    settings_after_reset = _safety(auth_client)["settings"]
    assert settings_after_reset["grace_period_days"] == 0
    assert settings_after_reset["applies_to_profile_visibility"] is True

    off = auth_client.patch(
        "/v1/system/safety", json={"applies_to_profile_visibility": False}
    )
    assert off.status_code == 200, off.text
    body = off.json()
    # Applied, not deferred: nothing queued, nothing to wait for.
    assert body["applied"] == ["applies_to_profile_visibility"], body
    assert body["deferred"] == [], body
    assert body["pending_change"] is None, body
    assert _safety(auth_client)["settings"]["applies_to_profile_visibility"] is False
    assert _safety(auth_client)["pending_changes"] == []


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
# A queued loosening is cancelled with the reset
# ---------------------------------------------------------------------------


def test_reset_cancels_a_queued_loosening(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """A reset a pending request can silently undo is not a reset.

    Reducing the grace period is a loosening, so it parks in a
    `SafetyChangeRequest` and waits out the window it is trying to shorten.
    Before, the reset zeroed the grace period without looking at that request,
    and the sweep later wrote the stored `{grace_period_days: 3}` back: the
    account had a grace period again, days after support "fixed" it, with
    nothing in the audit log to explain it. The reset now cancels queued
    loosenings, which takes nothing away because it has already gone further
    than the request was asking for.
    """
    uid = _me(auth_client)
    _arm(auth_client, days=7)

    queued = auth_client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 3, "password": "testpassword123"},
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["deferred"] == ["grace_period_days"]

    changed = _reset(admin_client, uid)["changed_fields"]
    assert "pending_safety_changes" in changed, changed
    assert _safety(auth_client)["settings"]["grace_period_days"] == 0
    assert _safety(auth_client)["pending_changes"] == []

    # The sweep has nothing left to apply, so the reset stands.
    run = admin_client.post("/v1/admin/jobs/finalize_safety_changes/run")
    assert run.status_code == 200, run.text
    assert _safety(auth_client)["settings"]["grace_period_days"] == 0

    # The cancelled request is in the audit diff, not just gone.
    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={uid}&action=user_safety_reset"
    ).json()
    assert events[0]["before_json"]["pending_safety_changes"], events[0]
    assert events[0]["after_json"]["pending_safety_changes"] == []


# ---------------------------------------------------------------------------
# Staged exposures: the reset leaves them, cancel-exposures takes them
# ---------------------------------------------------------------------------


def test_reset_does_not_cancel_a_staged_publish(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """A grant parked behind the grace window still goes live after a reset.

    Kept, and now said out loud in the endpoint's docstring rather than left
    to be discovered: the owner asked to publish, and the reset is about their
    settings, not their intent. Support that wants the publication stopped has
    cancel-exposures for it, and GET pending to see it first.
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


# ---------------------------------------------------------------------------
# cancel-exposures: the un-exposing lever
# ---------------------------------------------------------------------------


def _staged_grant(c: httpx.Client) -> tuple[str, str]:
    """A system with a grant parked behind a 7-day visibility window."""
    _go_public(c)
    _arm(c, days=7)
    vid = _view(c, [_member(c)])
    attest = c.post("/v1/auth/me/attest-adult")
    assert attest.status_code == 200, attest.text
    granted = c.post(
        "/v1/share-grants",
        json={
            "view_id": vid,
            "subject_type": "public",
            "password": "testpassword123",
        },
    )
    assert granted.status_code == 201, granted.text
    assert granted.json()["grant"]["status"] == "pending", granted.text
    return vid, granted.json()["grant"]["id"]


def test_cancel_exposures_stops_a_staged_publish(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """The staged grant never goes live, even once its window has elapsed."""
    uid = _me(auth_client)
    vid, grant_id = _staged_grant(auth_client)
    assert len(_safety(auth_client)["pending_exposures"]) >= 1

    cancelled = admin_client.post(
        f"/v1/admin/users/{uid}/cancel-exposures", json={"reason": "audit"}
    )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["by_kind"].get("share_grant") == 1, body

    assert _safety(auth_client)["pending_exposures"] == []

    # The window passing changes nothing: there is no longer anything staged
    # for the sweep to find.
    _backdate_grant(grant_id)
    run = admin_client.post("/v1/admin/jobs/finalize_share_activations/run")
    assert run.status_code == 200, run.text
    audit = auth_client.get("/v1/sharing/audit").json()
    live = [e for e in audit["entries"] if e["view_id"] == vid]
    assert not live or live[0]["grant"]["status"] != "active", audit


def test_cancel_exposures_leaves_a_live_grant_alone(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """It removes future exposures only. Something already public stays public.

    The distinction that makes this lever safe to hand to support: cancelling
    a staged raise is not a takedown. Revoking what is already serving is a
    different action with a different audit trail, and this must not quietly
    become it.
    """
    uid = _me(auth_client)
    # Published BEFORE safety is armed, so the grant is live rather than staged.
    _go_public(auth_client)
    vid = _view(auth_client, [_member(auth_client)])
    attest = auth_client.post("/v1/auth/me/attest-adult")
    assert attest.status_code == 200, attest.text
    granted = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "public"}
    )
    assert granted.status_code == 201, granted.text
    assert granted.json()["grant"]["status"] == "active", granted.text

    cancelled = admin_client.post(
        f"/v1/admin/users/{uid}/cancel-exposures", json={"reason": "audit"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["cancelled_count"] == 0, cancelled.text

    audit = auth_client.get("/v1/sharing/audit").json()
    live = [e for e in audit["entries"] if e["view_id"] == vid]
    assert live and live[0]["grant"]["status"] == "active", audit


def test_cancel_exposures_is_idempotent_and_always_audited(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    uid = _me(auth_client)
    first = admin_client.post(
        f"/v1/admin/users/{uid}/cancel-exposures", json={"reason": "nothing staged"}
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"cancelled_count": 0, "by_kind": {}}

    # A no-op still writes the row: "support pressed this and nothing was
    # waiting" is itself worth being able to prove.
    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={uid}&action=user_exposures_cancelled"
    ).json()
    assert len(events) == 1, events
    assert events[0]["reason"] == "nothing staged"


def test_cancel_exposures_requires_a_reason(admin_client: httpx.Client, auth_client):
    uid = _me(auth_client)
    resp = admin_client.post(
        f"/v1/admin/users/{uid}/cancel-exposures", json={"reason": ""}
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# GET pending: so nobody presses a button blind
# ---------------------------------------------------------------------------


def test_pending_read_reports_all_three_kinds(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    uid = _me(auth_client)
    _staged_grant(auth_client)

    # A queued deletion and a queued loosening alongside the staged grant.
    mid = _member(auth_client)
    armed = auth_client.patch(
        "/v1/system/safety",
        json={"applies_to_members": True, "password": "testpassword123"},
    )
    assert armed.status_code == 200, armed.text
    deleted = auth_client.request(
        "DELETE",
        f"/v1/members/{mid}",
        json={"password": "testpassword123"},
    )
    assert deleted.status_code in (200, 202, 204), deleted.text
    queued = auth_client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 3, "password": "testpassword123"},
    )
    assert queued.status_code == 200, queued.text

    resp = admin_client.get(f"/v1/admin/users/{uid}/pending")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["pending_actions"]["count"] == 1, body
    assert body["pending_actions"]["by_type"] == {"member_delete": 1}, body
    assert body["pending_actions"]["earliest_finalize_after"] is not None
    assert body["pending_changes"]["count"] == 1, body
    assert body["pending_changes"]["earliest_finalize_after"] is not None
    assert body["pending_exposures"]["count"] >= 1, body
    assert body["pending_exposures"]["by_kind"].get("share_grant") == 1, body
    assert body["pending_exposures"]["earliest_activates_at"] is not None


def test_pending_read_names_nothing(
    admin_client: httpx.Client, auth_client: httpx.Client
):
    """Counts and timestamps only.

    An admin screen answering "is anything waiting, and since when" has no
    business saying which member is being deleted. Asserted on the serialised
    body rather than field by field, so a future field that leaks a name or an
    id has to trip this.
    """
    uid = _me(auth_client)
    mid = _member(auth_client, "Distinctive Name For Leak Check")
    armed = auth_client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 7, "applies_to_members": True},
    )
    assert armed.status_code == 200, armed.text
    assert auth_client.delete(f"/v1/members/{mid}").status_code in (200, 202)

    raw = admin_client.get(f"/v1/admin/users/{uid}/pending").text
    assert "Distinctive Name For Leak Check" not in raw
    assert mid not in raw


def test_pending_read_404s_for_an_unknown_user(admin_client: httpx.Client):
    resp = admin_client.get(f"/v1/admin/users/{uuid.uuid4()}/pending")
    assert resp.status_code == 404, resp.text
