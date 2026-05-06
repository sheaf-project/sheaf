"""Integration tests for the reminders API."""

import httpx


def _system_id(client: httpx.Client) -> str:
    resp = client.get("/v1/systems/me")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _create_channel(client: httpx.Client) -> str:
    """Spin up a webhook channel for the auth_client's system; return its id.

    Webhook channels go active on creation (no activation step), which is
    the cheapest way to give the reminders endpoints a valid channel to
    reference in tests.
    """
    sid = _system_id(client)
    tok = client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": "test"}
    ).json()
    resp = client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={
            "name": "Test webhook",
            "destination_type": "webhook",
            "destination_config": {"url": "https://example.com/webhook"},
            "webhook_secret": "supersecret",
            "base_all_members": True,
            "trigger_on_start": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["channel"]["id"]


# --- Create + read --------------------------------------------------------


def test_create_repeated_daily_reminder(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    resp = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Daily meds",
            "title": "Take meds",
            "body": "10mg of X, 5mg of Y",
            "trigger_type": "repeated",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "schedule_tz": "UTC",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Daily meds"
    assert body["title"] == "Take meds"
    assert body["body"] == "10mg of X, 5mg of Y"
    assert body["trigger_type"] == "repeated"
    assert body["schedule_kind"] == "daily"
    assert body["scope"] == "system"
    assert body["next_fire_at"] is not None


def test_create_automated_reminder(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    resp = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Stretch ping",
            "title": "Get up and stretch",
            "trigger_type": "automated",
            "trigger_event": "start",
            "delay_seconds": 1800,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["trigger_type"] == "automated"
    assert body["delay_seconds"] == 1800
    assert body["next_fire_at"] is None  # automated has no schedule


def test_create_with_advanced_cron_takes_precedence(
    auth_client: httpx.Client,
):
    channel_id = _create_channel(auth_client)
    resp = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Mondays",
            "title": "Weekly review",
            "trigger_type": "repeated",
            "cron_expression": "0 9 * * 1",
            "schedule_tz": "UTC",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["cron_expression"] == "0 9 * * 1"
    assert resp.json()["next_fire_at"] is not None


def test_list_returns_reminders(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "First",
            "title": "Hello",
            "trigger_type": "repeated",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "schedule_tz": "UTC",
        },
    )
    auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Second",
            "title": "World",
            "trigger_type": "automated",
            "trigger_event": "any",
            "delay_seconds": 60,
        },
    )
    resp = auth_client.get("/v1/reminders")
    assert resp.status_code == 200
    names = sorted(r["name"] for r in resp.json())
    assert names == ["First", "Second"]


# --- Validation -----------------------------------------------------------


def test_reject_repeated_without_schedule_or_cron(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    resp = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Bad",
            "title": "x",
            "trigger_type": "repeated",
        },
    )
    assert resp.status_code == 400


def test_reject_automated_without_delay(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    resp = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Bad",
            "title": "x",
            "trigger_type": "automated",
            "trigger_event": "start",
        },
    )
    assert resp.status_code == 400


def test_reject_invalid_cron(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    resp = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Bad",
            "title": "x",
            "trigger_type": "repeated",
            "cron_expression": "not a cron",
            "schedule_tz": "UTC",
        },
    )
    assert resp.status_code == 400


def test_reject_invalid_timezone(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    resp = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Bad",
            "title": "x",
            "trigger_type": "repeated",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "schedule_tz": "Mars/Olympus",
        },
    )
    assert resp.status_code == 400


def test_reject_other_users_channel(auth_client: httpx.Client):
    """Channels are owned by a system; you can't aim a reminder at a
    channel that doesn't belong to you."""
    import uuid

    bogus_channel_id = str(uuid.uuid4())
    resp = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": bogus_channel_id,
            "name": "x",
            "title": "x",
            "trigger_type": "repeated",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "schedule_tz": "UTC",
        },
    )
    assert resp.status_code == 404


# --- Update --------------------------------------------------------------


def test_update_changes_fields(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    created = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Old",
            "title": "Old title",
            "trigger_type": "repeated",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "schedule_tz": "UTC",
        },
    ).json()

    resp = auth_client.patch(
        f"/v1/reminders/{created['id']}",
        json={
            "name": "New",
            "title": "New title",
            "schedule_time": "21:00",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "New"
    assert body["title"] == "New title"
    assert body["schedule_time"] == "21:00"


def test_disable_reminder(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    created = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "x",
            "title": "x",
            "trigger_type": "automated",
            "trigger_event": "any",
            "delay_seconds": 60,
        },
    ).json()
    resp = auth_client.patch(
        f"/v1/reminders/{created['id']}", json={"enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


# --- Delete --------------------------------------------------------------


def test_delete_reminder(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    created = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "x",
            "title": "x",
            "trigger_type": "automated",
            "trigger_event": "any",
            "delay_seconds": 60,
        },
    ).json()
    resp = auth_client.delete(f"/v1/reminders/{created['id']}")
    assert resp.status_code == 204

    resp = auth_client.get(f"/v1/reminders/{created['id']}")
    assert resp.status_code == 404


# --- Member-scoped reminders ---------------------------------------------


def test_member_scope_round_trip(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    alice = auth_client.post("/v1/members", json={"name": "Alice"}).json()
    bob = auth_client.post("/v1/members", json={"name": "Bob"}).json()

    resp = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Alice or Bob daily",
            "title": "Daily",
            "trigger_type": "repeated",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "schedule_tz": "UTC",
            "scope": "member",
            "scope_member_ids": [alice["id"], bob["id"]],
            "digest_when_absent": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scope"] == "member"
    assert set(body["scope_member_ids"]) == {alice["id"], bob["id"]}
    assert body["digest_when_absent"] is True


def test_scope_member_ids_must_belong_to_system(auth_client: httpx.Client):
    import uuid

    channel_id = _create_channel(auth_client)
    resp = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "x",
            "title": "x",
            "trigger_type": "repeated",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "schedule_tz": "UTC",
            "scope": "member",
            "scope_member_ids": [str(uuid.uuid4())],
        },
    )
    assert resp.status_code == 400


# --- next-fire endpoint --------------------------------------------------


def test_next_fire_endpoint(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    created = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Daily",
            "title": "x",
            "trigger_type": "repeated",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "schedule_tz": "UTC",
        },
    ).json()
    resp = auth_client.get(f"/v1/reminders/{created['id']}/next-fire")
    assert resp.status_code == 200
    assert resp.json()["next_fire_at"] is not None


# --- Export inclusion ----------------------------------------------------


def test_reminders_appear_in_data_export(auth_client: httpx.Client):
    """Reminders are config (not transient state), so the GDPR
    Article 20 export should carry them. Title/body decrypt to plaintext
    for the export."""
    channel_id = _create_channel(auth_client)
    auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "Daily check",
            "title": "Check your meds",
            "body": "Plaintext body content",
            "trigger_type": "repeated",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "schedule_tz": "UTC",
        },
    )
    export = auth_client.get("/v1/export").json()
    assert "reminders" in export
    assert len(export["reminders"]) == 1
    row = export["reminders"][0]
    assert row["name"] == "Daily check"
    assert row["title"] == "Check your meds"
    assert row["body"] == "Plaintext body content"
    assert row["schedule_kind"] == "daily"
    # Pending queue rows are runtime state, not exported.
    assert "pending" not in row
    assert "last_fired_at" not in row


# --- next-fire endpoint redux -------------------------------------------


def test_next_fire_for_automated_returns_null(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client)
    created = auth_client.post(
        "/v1/reminders",
        json={
            "channel_id": channel_id,
            "name": "x",
            "title": "x",
            "trigger_type": "automated",
            "trigger_event": "any",
            "delay_seconds": 60,
        },
    ).json()
    resp = auth_client.get(f"/v1/reminders/{created['id']}/next-fire")
    assert resp.status_code == 200
    assert resp.json()["next_fire_at"] is None
