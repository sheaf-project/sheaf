"""Integration tests for the polls API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx


def _member(
    client: httpx.Client, name: str, *, is_custom_front: bool = False
) -> str:
    resp = client.post(
        "/v1/members",
        json={"name": name, "is_custom_front": is_custom_front},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _front(client: httpx.Client, member_ids: list[str]) -> dict:
    resp = client.post("/v1/fronts", json={"member_ids": member_ids})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _closes_in(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _create_poll(
    client: httpx.Client,
    *,
    question: str = "Dinner?",
    kind: str = "single_choice",
    visibility: str = "live",
    closes_at: str | None = None,
    options: list[str] | None = None,
    include_custom_fronts: bool = False,
    restrict_voting_to_fronters: bool = False,
) -> dict:
    resp = client.post(
        "/v1/polls",
        json={
            "question": question,
            "kind": kind,
            "results_visibility": visibility,
            "closes_at": closes_at or _closes_in(86400),
            "include_custom_fronts": include_custom_fronts,
            "restrict_voting_to_fronters": restrict_voting_to_fronters,
            "options": [{"text": t} for t in (options or ["Pizza", "Sushi"])],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Create + read --------------------------------------------------------


def test_create_poll(auth_client: httpx.Client):
    poll = _create_poll(auth_client)
    assert poll["question"] == "Dinner?"
    assert poll["kind"] == "single_choice"
    assert poll["results_visibility"] == "live"
    assert len(poll["options"]) == 2
    assert poll["is_closed"] is False
    assert poll["total_votes"] == 0
    # Live: tally and votes are present (empty).
    assert poll["tally"] == [
        {"option_id": poll["options"][0]["id"], "count": 0},
        {"option_id": poll["options"][1]["id"], "count": 0},
    ]


def test_list_polls(auth_client: httpx.Client):
    _create_poll(auth_client, question="A?")
    _create_poll(auth_client, question="B?")
    resp = auth_client.get("/v1/polls")
    assert resp.status_code == 200
    qs = sorted(p["question"] for p in resp.json())
    assert qs == ["A?", "B?"]


def test_end_only_hides_results_until_close(auth_client: httpx.Client):
    poll = _create_poll(auth_client, visibility="end_only")
    fetched = auth_client.get(f"/v1/polls/{poll['id']}").json()
    assert fetched["tally"] is None
    assert fetched["votes"] is None


# --- Validation -----------------------------------------------------------


def test_reject_too_short_close_window(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/polls",
        json={
            "question": "x",
            "kind": "single_choice",
            "results_visibility": "live",
            "closes_at": _closes_in(60),  # under 1h minimum
            "options": [{"text": "a"}, {"text": "b"}],
        },
    )
    assert resp.status_code == 400


def test_reject_duplicate_options(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/polls",
        json={
            "question": "x",
            "kind": "single_choice",
            "results_visibility": "live",
            "closes_at": _closes_in(86400),
            "options": [{"text": "Same"}, {"text": " same "}],
        },
    )
    assert resp.status_code == 422


def test_reject_too_few_options(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/polls",
        json={
            "question": "x",
            "kind": "single_choice",
            "results_visibility": "live",
            "closes_at": _closes_in(86400),
            "options": [{"text": "only one"}],
        },
    )
    assert resp.status_code == 422


# --- Voting ---------------------------------------------------------------


def test_cast_vote_requires_fronting_when_restricted(auth_client: httpx.Client):
    """A poll created with restrict_voting_to_fronters=True only accepts
    votes from members currently in the front."""
    alice = _member(auth_client, "Alice")
    bob = _member(auth_client, "Bob")
    _front(auth_client, [alice])  # only alice is up

    poll = _create_poll(auth_client, restrict_voting_to_fronters=True)
    assert poll["restrict_voting_to_fronters"] is True

    # Attempt to vote as Bob (not fronting)
    resp = auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={
            "voted_as_member_id": bob,
            "option_ids": [poll["options"][0]["id"]],
        },
    )
    assert resp.status_code == 400
    assert "front" in resp.json()["detail"].lower()


def test_cast_vote_open_to_non_fronting_member_by_default(auth_client: httpx.Client):
    """The default poll (restrict_voting_to_fronters=False) accepts votes
    from any system member, regardless of front state — matches the
    journals authoring model."""
    alice = _member(auth_client, "Alice")
    bob = _member(auth_client, "Bob")
    _front(auth_client, [alice])  # only alice is up

    poll = _create_poll(auth_client)
    assert poll["restrict_voting_to_fronters"] is False

    # Bob is not fronting but the poll is open to all members.
    resp = auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={
            "voted_as_member_id": bob,
            "option_ids": [poll["options"][0]["id"]],
        },
    )
    assert resp.status_code == 200, resp.text


def test_withdraw_vote_open_to_non_fronting_member_by_default(
    auth_client: httpx.Client,
):
    """Withdraw obeys the same gate: by default, non-fronting members can
    withdraw a vote they cast earlier."""
    alice = _member(auth_client, "Alice")
    bob = _member(auth_client, "Bob")
    _front(auth_client, [alice, bob])
    poll = _create_poll(auth_client)
    resp = auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={
            "voted_as_member_id": bob,
            "option_ids": [poll["options"][0]["id"]],
        },
    )
    assert resp.status_code == 200, resp.text
    # Bob steps out of the front.
    _front(auth_client, [alice])

    resp = auth_client.delete(f"/v1/polls/{poll['id']}/votes/{bob}")
    assert resp.status_code == 204, resp.text


def test_custom_front_exclusion_still_applies_when_unrestricted(
    auth_client: httpx.Client,
):
    """Even with restrict_voting_to_fronters=False, a custom-front member
    can't vote unless include_custom_fronts is also set."""
    nap = _member(auth_client, "Asleep", is_custom_front=True)
    poll = _create_poll(auth_client)  # neither flag set

    resp = auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={
            "voted_as_member_id": nap,
            "option_ids": [poll["options"][0]["id"]],
        },
    )
    assert resp.status_code == 400
    assert "custom front" in resp.json()["detail"].lower()


def test_cast_vote_succeeds_for_fronting_member(auth_client: httpx.Client):
    alice = _member(auth_client, "Alice")
    _front(auth_client, [alice])
    poll = _create_poll(auth_client)

    resp = auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={
            "voted_as_member_id": alice,
            "option_ids": [poll["options"][0]["id"]],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["voted_as_member_id"] == alice
    assert body["option_ids"] == [poll["options"][0]["id"]]

    # Tally reflects the vote on next read.
    after = auth_client.get(f"/v1/polls/{poll['id']}").json()
    assert after["total_votes"] == 1


def test_single_choice_rejects_multi_select(auth_client: httpx.Client):
    alice = _member(auth_client, "Alice")
    _front(auth_client, [alice])
    poll = _create_poll(auth_client, kind="single_choice")

    resp = auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={
            "voted_as_member_id": alice,
            "option_ids": [
                poll["options"][0]["id"],
                poll["options"][1]["id"],
            ],
        },
    )
    assert resp.status_code == 400


def test_multi_choice_accepts_multiple(auth_client: httpx.Client):
    alice = _member(auth_client, "Alice")
    _front(auth_client, [alice])
    poll = _create_poll(
        auth_client,
        kind="multi_choice",
        options=["Pizza", "Sushi", "Tacos"],
    )

    resp = auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={
            "voted_as_member_id": alice,
            "option_ids": [
                poll["options"][0]["id"],
                poll["options"][2]["id"],
            ],
        },
    )
    assert resp.status_code == 200, resp.text


def test_change_vote_logs_change_event(auth_client: httpx.Client):
    alice = _member(auth_client, "Alice")
    _front(auth_client, [alice])
    poll = _create_poll(auth_client)
    opt_a = poll["options"][0]["id"]
    opt_b = poll["options"][1]["id"]

    auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={"voted_as_member_id": alice, "option_ids": [opt_a]},
    )
    auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={"voted_as_member_id": alice, "option_ids": [opt_b]},
    )

    audit = auth_client.get(f"/v1/polls/{poll['id']}/audit").json()
    assert audit["is_visible"] is True
    actions = [e["action"] for e in audit["events"]]
    assert actions == ["cast", "change"]
    # Only one current vote row remains
    fetched = auth_client.get(f"/v1/polls/{poll['id']}").json()
    assert fetched["total_votes"] == 1
    assert fetched["votes"][0]["option_ids"] == [opt_b]


def test_withdraw_vote(auth_client: httpx.Client):
    alice = _member(auth_client, "Alice")
    _front(auth_client, [alice])
    poll = _create_poll(auth_client)
    opt_a = poll["options"][0]["id"]

    auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={"voted_as_member_id": alice, "option_ids": [opt_a]},
    )
    resp = auth_client.delete(f"/v1/polls/{poll['id']}/votes/{alice}")
    assert resp.status_code == 204

    fetched = auth_client.get(f"/v1/polls/{poll['id']}").json()
    assert fetched["total_votes"] == 0

    audit = auth_client.get(f"/v1/polls/{poll['id']}/audit").json()
    actions = [e["action"] for e in audit["events"]]
    assert actions == ["cast", "withdraw"]


def test_audit_log_records_fronting_snapshot(auth_client: httpx.Client):
    alice = _member(auth_client, "Alice")
    bob = _member(auth_client, "Bob")
    _front(auth_client, [alice, bob])  # Alice + Bob co-fronting
    poll = _create_poll(auth_client)
    opt = poll["options"][0]["id"]

    auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={"voted_as_member_id": alice, "option_ids": [opt]},
    )

    audit = auth_client.get(f"/v1/polls/{poll['id']}/audit").json()
    assert len(audit["events"]) == 1
    snapshot = audit["events"][0]["fronting_member_ids"]
    assert set(snapshot) == {alice, bob}


def test_audit_hidden_for_end_only_until_close(auth_client: httpx.Client):
    poll = _create_poll(auth_client, visibility="end_only")
    audit = auth_client.get(f"/v1/polls/{poll['id']}/audit").json()
    assert audit["is_visible"] is False
    assert audit["events"] == []


# --- Delete --------------------------------------------------------------


def test_delete_poll(auth_client: httpx.Client):
    poll = _create_poll(auth_client)
    resp = auth_client.delete(f"/v1/polls/{poll['id']}")
    assert resp.status_code == 204
    assert auth_client.get(f"/v1/polls/{poll['id']}").status_code == 404


# --- Export inclusion ----------------------------------------------------


def test_polls_appear_in_export(auth_client: httpx.Client):
    """Polls are config + audit log, not transient state — Article 20
    export should carry both. Question and option text decrypt to
    plaintext for the export."""
    alice = _member(auth_client, "Alice")
    _front(auth_client, [alice])
    poll = _create_poll(auth_client, question="Plaintext?", options=["A", "B"])
    opt_a = poll["options"][0]["id"]
    auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={"voted_as_member_id": alice, "option_ids": [opt_a]},
    )

    export = auth_client.get("/v1/export").json()
    assert "polls" in export
    polls = export["polls"]
    assert len(polls) == 1
    row = polls[0]
    assert row["question"] == "Plaintext?"
    option_texts = sorted(o["text"] for o in row["options"])
    assert option_texts == ["A", "B"]
    assert len(row["votes"]) == 1
    assert len(row["events"]) == 1


def test_custom_fronts_excluded_by_default(auth_client: httpx.Client):
    """A custom-front member is in the front, but the poll defaults to
    excluding custom fronts. Their vote attempt is rejected; their
    non-custom co-fronter can still vote."""
    asleep = auth_client.post(
        "/v1/members",
        json={"name": "Asleep", "is_custom_front": True},
    ).json()["id"]
    alice = _member(auth_client, "Alice")
    _front(auth_client, [asleep, alice])

    poll = _create_poll(auth_client)
    assert poll["include_custom_fronts"] is False

    # Asleep is denied
    rejected = auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={
            "voted_as_member_id": asleep,
            "option_ids": [poll["options"][0]["id"]],
        },
    )
    assert rejected.status_code == 400
    assert "custom front" in rejected.json()["detail"].lower()

    # Alice is allowed
    accepted = auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={
            "voted_as_member_id": alice,
            "option_ids": [poll["options"][0]["id"]],
        },
    )
    assert accepted.status_code == 200


def test_custom_fronts_allowed_when_opted_in(auth_client: httpx.Client):
    asleep = auth_client.post(
        "/v1/members",
        json={"name": "Asleep", "is_custom_front": True},
    ).json()["id"]
    _front(auth_client, [asleep])

    poll = _create_poll(auth_client, include_custom_fronts=True)
    resp = auth_client.post(
        f"/v1/polls/{poll['id']}/votes",
        json={
            "voted_as_member_id": asleep,
            "option_ids": [poll["options"][0]["id"]],
        },
    )
    assert resp.status_code == 200, resp.text


def test_safety_settings_exposes_polls_toggle(auth_client: httpx.Client):
    resp = auth_client.get("/v1/system/safety")
    assert resp.status_code == 200
    settings = resp.json()["settings"]
    assert "applies_to_polls" in settings


# --- Server-config + tier limits -----------------------------------------


def test_server_config_exposes_tier_limits(auth_client: httpx.Client):
    resp = auth_client.get("/v1/polls/server-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in (
        "tier",
        "min_close_seconds",
        "max_close_seconds",
        "default_retention_days",
        "max_retention_days",
        "max_concurrent_open_polls",
    ):
        assert key in body, key


def test_reject_retention_over_tier_cap(auth_client: httpx.Client):
    cfg = auth_client.get("/v1/polls/server-config").json()
    cap = cfg["max_retention_days"]
    if cap == 0:
        # Selfhosted-style deployment: no cap, nothing to test.
        return
    resp = auth_client.post(
        "/v1/polls",
        json={
            "question": "x",
            "kind": "single_choice",
            "results_visibility": "live",
            "closes_at": _closes_in(86400),
            "retention_days": cap + 1,
            "options": [{"text": "a"}, {"text": "b"}],
        },
    )
    assert resp.status_code == 400


def test_concurrent_cap_enforced(auth_client: httpx.Client):
    cfg = auth_client.get("/v1/polls/server-config").json()
    cap = cfg["max_concurrent_open_polls"]
    if cap == 0:
        return
    for i in range(cap):
        _create_poll(auth_client, question=f"P{i}")
    # The (cap+1)th open poll should be refused.
    resp = auth_client.post(
        "/v1/polls",
        json={
            "question": "overflow",
            "kind": "single_choice",
            "results_visibility": "live",
            "closes_at": _closes_in(86400),
            "options": [{"text": "a"}, {"text": "b"}],
        },
    )
    assert resp.status_code == 403


# --- Retention purge nothing-silent trace ---------------------------------


def test_purge_expired_polls_writes_activity_trace(auth_client: httpx.Client):
    """Purging an expired poll leaves a content-free RETENTION_PRUNED activity
    event for the owning user with the purged count, and the cascade removes
    the poll's options."""
    import asyncio
    import os
    import uuid

    me = auth_client.get("/v1/auth/me").json()
    system = auth_client.get("/v1/systems/me").json()
    user_id = uuid.UUID(me["id"])
    system_id = uuid.UUID(system["id"])

    async def run() -> tuple[int, bool, bool, int]:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.activity_event import ActivityAction, ActivityEvent
        from sheaf.models.poll import Poll, PollOption
        from sheaf.services.polls import purge_expired_polls

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        session_factory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        now = datetime.now(UTC)
        try:
            # Insert an already-expired poll (closed 10 days ago, 1-day
            # retention) directly, so it is eligible for the sweep.
            async with session_factory() as db:
                poll = Poll(
                    id=uuid.uuid4(),
                    system_id=system_id,
                    question="expired-poll",
                    description=None,
                    kind="single_choice",
                    results_visibility="live",
                    closes_at=now - timedelta(days=10),
                    retention_days=1,
                )
                db.add(poll)
                await db.flush()
                db.add(
                    PollOption(
                        id=uuid.uuid4(),
                        poll_id=poll.id,
                        text="opt",
                        position=0,
                    )
                )
                await db.commit()
                poll_id = poll.id

            async with session_factory() as db:
                purged = await purge_expired_polls(db)
                await db.commit()

            async with session_factory() as db:
                poll_gone = (await db.get(Poll, poll_id)) is None
                opt_result = await db.execute(
                    select(PollOption).where(PollOption.poll_id == poll_id)
                )
                options_gone = not opt_result.scalars().all()

                ev_result = await db.execute(
                    select(ActivityEvent).where(
                        ActivityEvent.user_id == user_id,
                        ActivityEvent.action == ActivityAction.RETENTION_PRUNED,
                    )
                )
                traces = [
                    e.detail["polls_purged"]
                    for e in ev_result.scalars().all()
                    if e.detail and "polls_purged" in e.detail
                ]
            traced = traces[0] if traces else 0
            return purged, poll_gone, options_gone, traced
        finally:
            await engine.dispose()

    purged, poll_gone, options_gone, traced = asyncio.run(run())
    assert purged >= 1
    assert poll_gone, "expired poll survived the purge"
    assert options_gone, "cascade did not remove the poll's options"
    assert traced >= 1, "no RETENTION_PRUNED trace with polls_purged for the user"
