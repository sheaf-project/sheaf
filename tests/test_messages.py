"""Integration tests for the board messages feature."""

from __future__ import annotations

import httpx

# --- Helpers ----------------------------------------------------------------


def _create_member(client: httpx.Client, name: str) -> str:
    resp = client.post("/v1/members", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _post(
    client: httpx.Client,
    *,
    author_member_id: str,
    body: str,
    board_kind: str = "system",
    board_member_id: str | None = None,
    parent_message_id: str | None = None,
) -> dict:
    payload: dict = {
        "board_kind": board_kind,
        "author_member_id": author_member_id,
        "body": body,
    }
    if board_member_id is not None:
        payload["board_member_id"] = board_member_id
    if parent_message_id is not None:
        payload["parent_message_id"] = parent_message_id
    resp = client.post("/v1/messages", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- System board ----------------------------------------------------------


def test_read_state_get_or_create_is_race_safe(auth_client: httpx.Client):
    """Opening the messages page fires several board-touching requests in
    parallel, all funnelling through get_or_create_read_state. The old
    select-then-insert raced: per-member boards 500'd on the unique index
    and the system board (NULL board_member_id) accumulated duplicate rows
    that later broke scalar_one_or_none. Hammer both board kinds
    concurrently and assert no 5xx, then confirm follow-up reads still
    resolve to a single row."""
    import concurrent.futures

    member_id = _create_member(auth_client, "Racer")
    auth_header = auth_client.headers["Authorization"]
    base_url = str(auth_client.base_url)

    def hit(path: str) -> int:
        with httpx.Client(
            base_url=base_url, headers={"Authorization": auth_header}
        ) as c:
            return c.get(path).status_code

    system = f"/v1/messages?board_kind=system&caller_member_id={member_id}"
    wall = (
        f"/v1/messages?board_kind=member&board_member_id={member_id}"
        f"&caller_member_id={member_id}"
    )
    paths = [system, wall] * 8

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        statuses = list(ex.map(hit, paths))

    assert all(s == 200 for s in statuses), statuses
    # No duplicate rows left behind: subsequent reads still succeed
    # (a MultipleResultsFound would surface here as a 500).
    assert auth_client.get(system).status_code == 200
    assert (
        auth_client.get(
            f"/v1/messages/unread?caller_member_id={member_id}"
        ).status_code
        == 200
    )


def test_post_to_system_board(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    body = _post(auth_client, author_member_id=alice, body="hello system")
    assert body["board_kind"] == "system"
    assert body["board_member_id"] is None
    assert body["body"] == "hello system"
    assert body["author_member_id"] == alice
    assert body["author_member_name"] == "Alice"


def test_post_to_member_wall(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")
    body = _post(
        auth_client,
        author_member_id=alice,
        body="hi bob",
        board_kind="member",
        board_member_id=bob,
    )
    assert body["board_kind"] == "member"
    assert body["board_member_id"] == bob
    assert body["author_member_id"] == alice


def test_member_wall_requires_board_member_id(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    resp = auth_client.post(
        "/v1/messages",
        json={
            "board_kind": "member",
            "author_member_id": alice,
            "body": "x",
        },
    )
    assert resp.status_code == 400


def test_system_board_rejects_board_member_id(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    resp = auth_client.post(
        "/v1/messages",
        json={
            "board_kind": "system",
            "board_member_id": alice,
            "author_member_id": alice,
            "body": "x",
        },
    )
    assert resp.status_code == 400


def test_author_must_belong_to_system(auth_client: httpx.Client):
    import uuid

    resp = auth_client.post(
        "/v1/messages",
        json={
            "board_kind": "system",
            "author_member_id": str(uuid.uuid4()),
            "body": "ghost",
        },
    )
    assert resp.status_code == 400


# --- Listing ---------------------------------------------------------------


def test_list_messages_newest_first(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    first = _post(auth_client, author_member_id=alice, body="first")
    second = _post(auth_client, author_member_id=alice, body="second")
    resp = auth_client.get(
        "/v1/messages",
        params={"board_kind": "system"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["board_kind"] == "system"
    assert body["board_member_id"] is None
    ids = [m["id"] for m in body["messages"]]
    # Most recent first.
    assert ids[0] == second["id"]
    assert ids[1] == first["id"]


def test_member_wall_isolated_from_system_board(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")
    _post(auth_client, author_member_id=alice, body="on system")
    _post(
        auth_client,
        author_member_id=alice,
        body="on bob's wall",
        board_kind="member",
        board_member_id=bob,
    )

    sys_msgs = auth_client.get(
        "/v1/messages", params={"board_kind": "system"}
    ).json()["messages"]
    bob_msgs = auth_client.get(
        "/v1/messages",
        params={"board_kind": "member", "board_member_id": bob},
    ).json()["messages"]

    assert [m["body"] for m in sys_msgs] == ["on system"]
    assert [m["body"] for m in bob_msgs] == ["on bob's wall"]


# --- Threading -------------------------------------------------------------


def test_reply_chain(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")
    parent = _post(auth_client, author_member_id=alice, body="anyone fronting?")
    reply = _post(
        auth_client,
        author_member_id=bob,
        body="bob here",
        parent_message_id=parent["id"],
    )
    assert reply["parent_message_id"] == parent["id"]
    assert reply["parent_preview"] == "anyone fronting?"
    assert reply["parent_author_member_name"] == "Alice"


def test_reply_must_be_on_same_board(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")
    parent = _post(auth_client, author_member_id=alice, body="on system")
    resp = auth_client.post(
        "/v1/messages",
        json={
            "board_kind": "member",
            "board_member_id": bob,
            "author_member_id": alice,
            "body": "wrong board",
            "parent_message_id": parent["id"],
        },
    )
    assert resp.status_code == 400


# --- Edit + revisions -----------------------------------------------------


def test_edit_message_captures_revision(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    msg = _post(auth_client, author_member_id=alice, body="before edit")
    resp = auth_client.patch(
        f"/v1/messages/{msg['id']}", json={"body": "after edit"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["body"] == "after edit"


def test_revision_history_lists_and_restores(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    msg = _post(auth_client, author_member_id=alice, body="v1")
    auth_client.patch(f"/v1/messages/{msg['id']}", json={"body": "v2"})
    auth_client.patch(f"/v1/messages/{msg['id']}", json={"body": "v3"})

    revs = auth_client.get(f"/v1/messages/{msg['id']}/revisions").json()
    bodies = [r["body"] for r in revs]
    # Two captures from edits, plus the auto-pinned v1 first revision.
    assert "v1" in bodies and "v2" in bodies
    # Newest captured first.
    assert revs[0]["body"] == "v2"

    # Restore the v1 revision and confirm the live body flips.
    v1_id = next(r["id"] for r in revs if r["body"] == "v1")
    restore = auth_client.post(
        f"/v1/messages/{msg['id']}/restore-revision",
        json={"revision_id": v1_id},
    )
    assert restore.status_code == 200, restore.text
    assert restore.json()["body"] == "v1"

    # Restoring captured the pre-restore "v3" as a fresh revision.
    revs_after = auth_client.get(f"/v1/messages/{msg['id']}/revisions").json()
    assert any(r["body"] == "v3" for r in revs_after)


def test_gc_revisions_trims_message_revisions(client: httpx.Client):
    """gc_revisions must include 'message' targets in its sweep, otherwise
    edit history grows unbounded. Mirrors the journal/bio coverage."""
    import asyncio
    import os
    import uuid as _uuid

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from sheaf.config import settings
    from sheaf.crypto import blind_index
    from sheaf.models.system import System
    from sheaf.models.user import User
    from sheaf.services.retention import gc_revisions

    email = f"msggc-{_uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"

    # Drop to free tier (count cap = 10) and disable auto-pin so the cap
    # alone drives the trim.
    async def _prep() -> None:
        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
            ).scalar_one()
            user.tier = "free"
            system = (
                await db.execute(select(System).where(System.user_id == user.id))
            ).scalar_one()
            system.auto_pin_first_revision = False
            await db.commit()
        await engine.dispose()

    asyncio.run(_prep())

    alice = _create_member(client, "Alice")
    msg = _post(client, author_member_id=alice, body="v0")
    for i in range(1, 16):
        client.patch(f"/v1/messages/{msg['id']}", json={"body": f"v{i}"})

    revs_before = client.get(f"/v1/messages/{msg['id']}/revisions").json()
    assert len(revs_before) == 15

    async def _run_gc() -> int:
        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                result = await gc_revisions(db)
                await db.commit()
                return result["items_processed"]
        finally:
            await engine.dispose()

    deleted = asyncio.run(_run_gc())
    assert deleted >= 5

    revs_after = client.get(f"/v1/messages/{msg['id']}/revisions").json()
    assert len(revs_after) == 10


def test_gc_revisions_sweeps_orphaned_message_revisions(client: httpx.Client):
    """Defensive orphan sweep: revisions whose message was hard-deleted
    bypassing the cascade helper should still be cleaned up."""
    import asyncio
    import os
    import uuid as _uuid

    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from sheaf.config import settings
    from sheaf.models.content_revision import ContentRevision
    from sheaf.models.message import Message
    from sheaf.services.retention import gc_revisions

    email = f"msgorphan-{_uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"

    alice = _create_member(client, "Alice")
    msg = _post(client, author_member_id=alice, body="v0")
    client.patch(f"/v1/messages/{msg['id']}", json={"body": "v1"})
    msg_id = _uuid.UUID(msg["id"])

    # Hard-delete the message row directly, bypassing the API cascade
    # helper. The revisions will be left orphaned.
    async def _orphan() -> int:
        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            await db.execute(delete(Message).where(Message.id == msg_id))
            await db.commit()
            count = (
                await db.execute(
                    select(ContentRevision).where(
                        ContentRevision.target_type == "message",
                        ContentRevision.target_id == msg_id,
                    )
                )
            ).all()
            await engine.dispose()
            return len(count)

    orphans = asyncio.run(_orphan())
    assert orphans >= 1

    async def _run_gc() -> None:
        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            await gc_revisions(db)
            await db.commit()
            remaining = (
                await db.execute(
                    select(ContentRevision).where(
                        ContentRevision.target_type == "message",
                        ContentRevision.target_id == msg_id,
                    )
                )
            ).all()
            assert remaining == []
        await engine.dispose()

    asyncio.run(_run_gc())


# --- Delete ----------------------------------------------------------------


def test_delete_single_message_leaves_replies(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")
    parent = _post(auth_client, author_member_id=alice, body="parent")
    reply = _post(
        auth_client,
        author_member_id=bob,
        body="reply",
        parent_message_id=parent["id"],
    )

    resp = auth_client.delete(f"/v1/messages/{parent['id']}")
    assert resp.status_code == 204

    listing = auth_client.get(
        "/v1/messages", params={"board_kind": "system"}
    ).json()
    ids = [m["id"] for m in listing["messages"]]
    assert parent["id"] not in ids
    assert reply["id"] in ids
    # Reply still rendered, parent_preview is now None (parent gone).
    reply_row = next(m for m in listing["messages"] if m["id"] == reply["id"])
    assert reply_row["parent_preview"] is None


def test_delete_thread_cascades(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")
    parent = _post(auth_client, author_member_id=alice, body="root")
    reply1 = _post(
        auth_client,
        author_member_id=bob,
        body="r1",
        parent_message_id=parent["id"],
    )
    reply2 = _post(
        auth_client,
        author_member_id=alice,
        body="r2",
        parent_message_id=reply1["id"],
    )

    resp = auth_client.delete(f"/v1/messages/{parent['id']}/thread")
    assert resp.status_code == 204

    listing = auth_client.get(
        "/v1/messages", params={"board_kind": "system"}
    ).json()
    ids = {m["id"] for m in listing["messages"]}
    assert parent["id"] not in ids
    assert reply1["id"] not in ids
    assert reply2["id"] not in ids


# --- Read-state + unread ---------------------------------------------------


def test_unread_count_for_caller_member(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")
    _post(auth_client, author_member_id=alice, body="m1")
    _post(auth_client, author_member_id=alice, body="m2")

    # Bob has never viewed — but a fresh read_state row defaults to "now",
    # so historical messages do not flash as unread on first view.
    resp = auth_client.get(
        "/v1/messages/unread", params={"caller_member_id": bob}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    sys_summary = next(
        s for s in body["by_board"] if s["board_kind"] == "system"
    )
    assert sys_summary["unread_count"] == 0

    # New message lands AFTER bob's read_state was created. Now unread.
    _post(auth_client, author_member_id=alice, body="after read_state")
    body = auth_client.get(
        "/v1/messages/unread", params={"caller_member_id": bob}
    ).json()
    sys_summary = next(
        s for s in body["by_board"] if s["board_kind"] == "system"
    )
    assert sys_summary["unread_count"] == 1
    assert body["total"] >= 1


def test_mark_seen_zeroes_unread(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")
    # Establish bob's read_state.
    auth_client.get(
        "/v1/messages/unread", params={"caller_member_id": bob}
    )
    _post(auth_client, author_member_id=alice, body="new since")
    pre = auth_client.get(
        "/v1/messages/unread", params={"caller_member_id": bob}
    ).json()
    assert pre["total"] == 1

    resp = auth_client.post(
        "/v1/messages/mark-seen",
        json={"member_id": bob, "board_kind": "system"},
    )
    assert resp.status_code == 204

    post = auth_client.get(
        "/v1/messages/unread", params={"caller_member_id": bob}
    ).json()
    assert post["total"] == 0


# --- Front-start prompt ----------------------------------------------------


def test_front_start_prompt_respects_opt_in(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")

    # Bob's read_state exists at "now" so future posts will count as unread.
    auth_client.get(
        "/v1/messages/unread", params={"caller_member_id": bob}
    )

    # Bob opts in to global only.
    auth_client.put(
        f"/v1/messages/notify-settings/{bob}",
        json={
            "notify_on_front_global": True,
            "notify_on_front_self": False,
            "notify_on_front_member_ids": [],
        },
    )

    _post(auth_client, author_member_id=alice, body="global ping")
    _post(
        auth_client,
        author_member_id=alice,
        body="bob wall ping",
        board_kind="member",
        board_member_id=bob,
    )

    body = auth_client.get(
        "/v1/messages/front-start-prompt", params={"member_id": bob}
    ).json()
    # Only the system-board entry should show up — bob did NOT opt in
    # to his own wall.
    assert len(body["summaries"]) == 1
    assert body["summaries"][0]["board_kind"] == "system"
    assert body["total_unread"] >= 1


def test_front_start_prompt_empty_when_no_opt_ins(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")
    _post(auth_client, author_member_id=alice, body="hi")
    body = auth_client.get(
        "/v1/messages/front-start-prompt", params={"member_id": bob}
    ).json()
    assert body["summaries"] == []
    assert body["total_unread"] == 0


# --- Notify settings round-trip --------------------------------------------


def test_notify_settings_validate_member_ids(auth_client: httpx.Client):
    import uuid

    alice = _create_member(auth_client, "Alice")
    resp = auth_client.put(
        f"/v1/messages/notify-settings/{alice}",
        json={
            "notify_on_front_global": False,
            "notify_on_front_self": True,
            "notify_on_front_member_ids": [str(uuid.uuid4())],
        },
    )
    assert resp.status_code == 400


# --- Boards listing --------------------------------------------------------


def test_boards_listing_orders_members_by_recent_message(
    auth_client: httpx.Client,
):
    alice = _create_member(auth_client, "Alice")
    bob = _create_member(auth_client, "Bob")
    carol = _create_member(auth_client, "Carol")
    # Post on Bob's wall first, then Carol's. Carol should sort first.
    _post(
        auth_client,
        author_member_id=alice,
        body="for bob",
        board_kind="member",
        board_member_id=bob,
    )
    _post(
        auth_client,
        author_member_id=alice,
        body="for carol",
        board_kind="member",
        board_member_id=carol,
    )

    boards = auth_client.get("/v1/messages/boards").json()
    # First entry is always the system board.
    assert boards[0]["board_kind"] == "system"
    member_entries = [b for b in boards if b["board_kind"] == "member"]
    # Carol → Bob → Alice (no messages, comes last).
    assert member_entries[0]["board_member_id"] == carol
    assert member_entries[1]["board_member_id"] == bob
    assert member_entries[2]["board_member_id"] == alice


def test_export_includes_messages(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    _post(auth_client, author_member_id=alice, body="exported")
    export = auth_client.get("/v1/export").json()
    assert "messages" in export
    bodies = [m["body"] for m in export["messages"]]
    assert "exported" in bodies


def test_safety_settings_exposes_messages_toggle(auth_client: httpx.Client):
    resp = auth_client.get("/v1/system/safety")
    assert resp.status_code == 200
    settings = resp.json()["settings"]
    assert "applies_to_messages" in settings


def test_message_revision_list_pagination_walks_all(auth_client: httpx.Client):
    """Message revision history is keyset-paginated like the front history:
    a small `limit` truncates the page, the X-Sheaf-* headers signal/drive
    the next page, and the walk yields the same rows as one big page. Body
    stays a plain array (backward compatible)."""
    alice = _create_member(auth_client, "Alice")
    msg = _post(auth_client, author_member_id=alice, body="v0")
    for i in range(1, 6):
        auth_client.patch(f"/v1/messages/{msg['id']}", json={"body": f"v{i}"})

    full = auth_client.get(
        f"/v1/messages/{msg['id']}/revisions", params={"limit": 500}
    ).json()
    full_ids = {r["id"] for r in full}
    assert len(full_ids) > 2, full_ids

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(20):  # generous loop bound
        params: dict[str, str] = {"limit": "2"}
        if cursor:
            params["cursor"] = cursor
        resp = auth_client.get(
            f"/v1/messages/{msg['id']}/revisions", params=params
        )
        assert resp.status_code == 200, resp.text
        page = resp.json()
        assert isinstance(page, list) and len(page) <= 2
        seen.extend(row["id"] for row in page)
        if resp.headers["X-Sheaf-Has-More"] != "true":
            break
        cursor = resp.headers["X-Sheaf-Next-Cursor"]

    assert set(seen) == full_ids, seen
    assert len(seen) == len(set(seen)), "page boundary produced a duplicate"


def test_message_revision_list_rejects_bad_cursor(auth_client: httpx.Client):
    alice = _create_member(auth_client, "Alice")
    msg = _post(auth_client, author_member_id=alice, body="x")
    resp = auth_client.get(
        f"/v1/messages/{msg['id']}/revisions", params={"cursor": "not-a-cursor"}
    )
    assert resp.status_code == 400, resp.text


def test_board_summary_preview_is_newest_body(auth_client: httpx.Client):
    """Board summary's last_message_preview must be the *newest* message
    body and message_count the full total. Guards the DISTINCT-ON latest
    body rewrite (which no longer array_aggs every body on the board)."""
    alice = _create_member(auth_client, "Alice")
    _post(auth_client, author_member_id=alice, body="oldest")
    _post(auth_client, author_member_id=alice, body="middle")
    _post(auth_client, author_member_id=alice, body="newest")

    boards = auth_client.get("/v1/messages/boards").json()
    system_board = next(b for b in boards if b["board_kind"] == "system")
    assert system_board["last_message_preview"] == "newest"
    assert system_board["message_count"] == 3
