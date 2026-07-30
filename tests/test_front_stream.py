"""Tests for the realtime front-change stream (SSE).

Split into:
  - Unit tests (no running stack): the authorized-system-set resolver shape,
    channel/key naming, config defaults, SSE framing, and payload
    serialization. These run under the dummy-URL invocation.
  - Integration tests (need the running app + Redis): function names contain
    "integration" so they can be deselected with `-k "not integration"`. They
    exercise the live endpoint through the shared test stack.
"""

import asyncio
import contextlib
import json
import os
import uuid
from contextlib import ExitStack

import httpx
import pytest

from sheaf.config import Settings
from sheaf.models.user import UserTier
from sheaf.services.front_stream import (
    authorized_front_system_ids,
    build_change_payload,
    build_snapshot_payload,
    connection_slots_key,
    format_comment,
    format_sse,
    front_channel,
    max_front_stream_connections_for_tier,
    serialize_front_state,
)
from sheaf.services.notifications.events import FrontState

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# Unit: authorized-system-set resolver
# ---------------------------------------------------------------------------

class _FakeScalarResult:
    def __init__(self, ids):
        self._ids = ids

    def scalars(self):
        return self

    def all(self):
        return list(self._ids)


class _FakeDB:
    """Minimal AsyncSession stand-in: execute() returns a canned scalar list."""

    def __init__(self, ids):
        self._ids = ids

    async def execute(self, *args, **kwargs):
        return _FakeScalarResult(self._ids)


class _FakeUser:
    id = uuid.uuid4()


async def test_authorized_front_system_ids_returns_list_of_single_system():
    sid = uuid.uuid4()
    result = await authorized_front_system_ids(_FakeUser(), _FakeDB([sid]))
    # A list resolver, not a scalar: one system today, N later without
    # reshaping the stream path.
    assert result == [sid]
    assert isinstance(result, list)


async def test_authorized_front_system_ids_empty_is_empty_list():
    result = await authorized_front_system_ids(_FakeUser(), _FakeDB([]))
    assert result == []


# ---------------------------------------------------------------------------
# Unit: channel + key naming
# ---------------------------------------------------------------------------

def test_front_channel_is_per_system():
    sid = uuid.uuid4()
    assert front_channel(sid) == f"sheaf:fronts:{sid}"


def test_connection_slots_key_is_per_account():
    assert connection_slots_key("acct-123") == "sheaf:stream:slots:acct-123"


# ---------------------------------------------------------------------------
# Unit: config defaults
# ---------------------------------------------------------------------------

def test_front_stream_config_defaults():
    # Assert the declared defaults, independent of any env override the live
    # test stack might carry.
    fields = Settings.model_fields
    assert fields["front_stream_enabled"].default is True
    assert fields["front_stream_max_connections_free"].default == 5
    assert fields["front_stream_max_connections_plus"].default == 10
    assert fields["front_stream_max_connections_selfhosted"].default == 0
    assert fields["front_stream_heartbeat_seconds"].default == 20
    assert fields["front_stream_auth_recheck_seconds"].default == 60


def test_max_front_stream_connections_for_tier_maps_each_tier():
    # Free/Plus are finite; self-hosted is unlimited (0) by default. Anything
    # unrecognised falls through to the free cap (the conservative default).
    assert (
        max_front_stream_connections_for_tier(UserTier.FREE)
        == Settings.model_fields["front_stream_max_connections_free"].default
    )
    assert (
        max_front_stream_connections_for_tier(UserTier.PLUS)
        == Settings.model_fields["front_stream_max_connections_plus"].default
    )
    assert (
        max_front_stream_connections_for_tier(UserTier.SELF_HOSTED)
        == Settings.model_fields["front_stream_max_connections_selfhosted"].default
    )


# ---------------------------------------------------------------------------
# Unit: SSE framing
# ---------------------------------------------------------------------------

def test_format_sse_full_frame():
    frame = format_sse('{"a":1}', event="snapshot", id="evt-1")
    assert frame == 'id: evt-1\nevent: snapshot\ndata: {"a":1}\n\n'


def test_format_sse_data_only():
    frame = format_sse("hello")
    assert frame == "data: hello\n\n"


def test_format_sse_multiline_data_splits_into_data_lines():
    frame = format_sse("line1\nline2", event="x")
    assert frame == "event: x\ndata: line1\ndata: line2\n\n"


def test_format_comment_is_sse_comment():
    assert format_comment() == ": ping\n\n"
    assert format_comment("hb") == ": hb\n\n"


# ---------------------------------------------------------------------------
# Unit: payload serialization (matches GET /v1/fronts member shape)
# ---------------------------------------------------------------------------

def _state(*member_ids):
    return FrontState(fronting_member_ids=frozenset(member_ids))


def test_serialize_front_state_is_sorted_str_member_ids():
    a, b = uuid.uuid4(), uuid.uuid4()
    out = serialize_front_state(_state(a, b))
    assert out == sorted([str(a), str(b)])
    assert all(isinstance(m, str) for m in out)


def test_build_snapshot_payload_shape():
    sid = uuid.uuid4()
    m = uuid.uuid4()
    payload = build_snapshot_payload(sid, _state(m))
    assert payload["system_id"] == str(sid)
    assert payload["fronting"] == [str(m)]
    # per-front detail defaults to empty when not supplied
    assert payload["fronts"] == []
    # event_id present and JSON-serializable
    assert "event_id" in payload
    json.dumps(payload)


def test_build_snapshot_payload_carries_fronts_detail():
    sid = uuid.uuid4()
    m = uuid.uuid4()
    fronts = [
        {
            "id": "f1",
            "member_ids": [str(m)],
            "started_at": "2026-07-23T12:00:00+00:00",
            "custom_status": None,
            "member_since": {str(m): "2026-07-23T12:00:00+00:00"},
        }
    ]
    payload = build_snapshot_payload(sid, _state(m), fronts=fronts)
    assert payload["fronts"] == fronts
    json.dumps(payload)


def test_build_change_payload_shape_carries_system_id_and_both_states():
    from datetime import UTC, datetime

    sid = uuid.uuid4()
    before_m = uuid.uuid4()
    after_m = uuid.uuid4()
    changed_at = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    event_id = uuid.uuid4()
    fronts = [
        {
            "id": "f1",
            "member_ids": [str(after_m)],
            "started_at": changed_at.isoformat(),
            "custom_status": "at work",
            "member_since": {str(after_m): changed_at.isoformat()},
        }
    ]
    payload = build_change_payload(
        sid,
        _state(before_m),
        _state(after_m),
        changed_at=changed_at,
        event_id=event_id,
        emit_ts=123.5,
        fronts=fronts,
    )
    assert payload["system_id"] == str(sid)
    assert payload["before"] == [str(before_m)]
    assert payload["after"] == [str(after_m)]
    assert payload["changed_at"] == changed_at.isoformat()
    assert payload["event_id"] == str(event_id)
    assert payload["emit_ts"] == 123.5
    # NEW: per-front detail is carried through verbatim
    assert payload["fronts"] == fronts
    # and defaults to [] when the caller omits it (back-compat)
    assert (
        build_change_payload(
            sid, _state(before_m), _state(after_m),
            changed_at=changed_at, event_id=event_id, emit_ts=1.0,
        )["fronts"]
        == []
    )
    json.dumps(payload)


# ---------------------------------------------------------------------------
# Integration helpers (need the running stack)
# ---------------------------------------------------------------------------

def _create_key(client: httpx.Client, scopes: list[str]) -> str:
    resp = client.post(
        "/v1/auth/keys", json={"name": "stream-test", "scopes": scopes}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]


def _create_member(client: httpx.Client, name: str) -> str:
    resp = client.post("/v1/members", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _read_sse_event(line_iter, *, skip_comments: bool = True) -> dict:
    """Assemble the next non-comment SSE event from a line iterator.

    Returns {"event": <type|None>, "data": <parsed json|raw>, "id": <id|None>}.
    Heartbeat comment lines (`: ping`) are skipped. Bounded by the underlying
    httpx read timeout, so a stalled stream raises rather than hanging.
    """
    event_type = None
    event_id = None
    data_lines: list[str] = []
    for raw in line_iter:
        if raw == "":
            # End of an event block. Ignore stray blanks / comment-only blocks.
            if event_type is None and not data_lines:
                continue
            data = "\n".join(data_lines)
            with contextlib.suppress(ValueError, TypeError):
                data = json.loads(data)
            return {"event": event_type, "data": data, "id": event_id}
        if raw.startswith(":"):
            if skip_comments:
                continue
        elif raw.startswith("event:"):
            event_type = raw[len("event:"):].strip()
        elif raw.startswith("data:"):
            data_lines.append(raw[len("data:"):].lstrip())
        elif raw.startswith("id:"):
            event_id = raw[len("id:"):].strip()
    raise AssertionError("stream ended before a complete SSE event arrived")


def test_stream_handler_holds_no_request_lifetime_db_session():
    """Regression: the SSE handler must NOT depend on `get_db`.

    `get_db` is a yield-dependency, torn down only after the response finishes,
    and a StreamingResponse finishes only when the stream closes. So a
    request-scoped session would pin a pooled Postgres connection
    idle-in-transaction for the entire stream and exhaust the pool, blocking
    every other request that needs the DB (this shipped once). The handler must
    resolve what it needs in a short-lived `async_session_factory()` session
    instead. A future re-add of `Depends(get_db)` here fails this test.
    """
    import inspect

    from sheaf.api.v1 import front_stream
    from sheaf.database import get_db

    deps = [
        getattr(p.default, "dependency", None)
        for p in inspect.signature(front_stream.stream_fronts).parameters.values()
    ]
    assert get_db not in deps, (
        "stream_fronts depends on get_db; a request-lifetime DB session pins a "
        "pooled connection for the whole stream. Use async_session_factory()."
    )


# ---------------------------------------------------------------------------
# Integration: snapshot + delta, scope, cap, disabled
# ---------------------------------------------------------------------------

def test_integration_stream_sends_snapshot_then_front_change(auth_client: httpx.Client):
    member = _create_member(auth_client, f"S-{uuid.uuid4().hex[:6]}")
    key = _create_key(auth_client, ["fronts:read", "fronts:write"])
    headers = {"Authorization": f"Bearer {key}"}

    with httpx.Client(base_url=BASE_URL) as stream_client, stream_client.stream(
        "GET", "/v1/fronts/stream", headers=headers, timeout=15.0
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        lines = resp.iter_lines()

        snapshot = _read_sse_event(lines)
        assert snapshot["event"] == "snapshot"
        assert "fronting" in snapshot["data"]
        assert "system_id" in snapshot["data"]

        # Drive a change on the same account via a separate client.
        r = auth_client.post("/v1/fronts", json={"member_ids": [member]})
        assert r.status_code == 201, r.text

        change = _read_sse_event(lines)
        assert change["event"] == "front_change"
        assert change["data"]["system_id"] == snapshot["data"]["system_id"]
        assert member in change["data"]["after"]


def test_integration_stream_frame_carries_per_front_detail(auth_client: httpx.Client):
    # The fronts[] array carries per-front composition (id, member_ids,
    # started_at, custom_status, member_since) so a client can tell "a member
    # joined a new front" from the frame - which the before/after union lists
    # can't express. Both the snapshot and every front_change carry it.
    a = _create_member(auth_client, f"A-{uuid.uuid4().hex[:6]}")
    b = _create_member(auth_client, f"B-{uuid.uuid4().hex[:6]}")
    key = _create_key(auth_client, ["fronts:read", "fronts:write"])
    headers = {"Authorization": f"Bearer {key}"}
    _front_keys = {"id", "member_ids", "started_at", "custom_status", "member_since"}

    with httpx.Client(base_url=BASE_URL) as stream_client, stream_client.stream(
        "GET", "/v1/fronts/stream", headers=headers, timeout=15.0
    ) as resp:
        assert resp.status_code == 200
        lines = resp.iter_lines()

        snapshot = _read_sse_event(lines)
        assert snapshot["event"] == "snapshot"
        assert "fronts" in snapshot["data"]  # per-front baseline on connect

        # Front A.
        assert (
            auth_client.post("/v1/fronts", json={"member_ids": [a]}).status_code
            == 201
        )
        change = _read_sse_event(lines)
        fronts = change["data"]["fronts"]
        assert isinstance(fronts, list) and fronts
        for f in fronts:
            assert set(f) >= _front_keys
        assert any(a in f["member_ids"] for f in fronts)

        # Now front A + B: B joins the fronting composition.
        assert (
            auth_client.post(
                "/v1/fronts", json={"member_ids": [a, b]}
            ).status_code
            == 201
        )
        change2 = _read_sse_event(lines)
        fronts2 = change2["data"]["fronts"]
        # Every fronting member in the union is accounted for across fronts[],
        # and each front maps its members to a member_since timestamp.
        detail_members = {m for f in fronts2 for m in f["member_ids"]}
        assert detail_members == set(change2["data"]["after"])
        assert b in detail_members
        for f in fronts2:
            for mid in f["member_ids"]:
                assert mid in f["member_since"]


def test_integration_stream_does_not_block_same_key_requests(auth_client: httpx.Client):
    """Regression: holding a stream open must not block other requests that use
    the SAME API key.

    get_current_user used to bump api_keys.last_used_at in the request session,
    which stays open for the whole stream - so the UPDATE held a write lock on
    the api_keys row, and a concurrent request with the same key blocked on it
    to a statement-timeout 500. last_used_at is now written in a separate
    committed session, so no lock is held. (The earlier snapshot+delta test
    missed this because its concurrent request used a different credential.)
    """
    _create_member(auth_client, f"K-{uuid.uuid4().hex[:6]}")
    key = _create_key(auth_client, ["fronts:read"])
    headers = {"Authorization": f"Bearer {key}"}

    with httpx.Client(base_url=BASE_URL) as stream_client, stream_client.stream(
        "GET", "/v1/fronts/stream", headers=headers, timeout=15.0
    ) as resp:
        assert resp.status_code == 200
        assert _read_sse_event(resp.iter_lines())["event"] == "snapshot"

        # While the stream is held open, a request with the SAME key must not
        # block on the api_keys row lock. Bounded tightly so the old lock-hold
        # (statement_timeout) fails fast rather than hanging the suite.
        with httpx.Client(base_url=BASE_URL) as poll_client:
            r = poll_client.get("/v1/fronts", headers=headers, timeout=10.0)
        assert r.status_code == 200, r.text


def test_integration_stream_requires_fronts_read_scope(auth_client: httpx.Client):
    # A key with an unrelated scope must be rejected with 403.
    key = _create_key(auth_client, ["members:read"])
    headers = {"Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=BASE_URL) as c:
        resp = c.get("/v1/fronts/stream", headers=headers, timeout=10.0)
        assert resp.status_code == 403


def _set_tier(client: httpx.Client, tier: UserTier) -> None:
    """Set the user's tier directly in the DB.

    The per-account stream cap is tier-derived (no per-user override column),
    and the selfhosted test stack registers users as SELF_HOSTED = unlimited,
    so a cap test must pin a finite tier (FREE) to exercise the 429 path.
    Mirrors _set_member_limit in the import tests.
    """
    email = client.get("/v1/auth/me").json()["email"]

    async def _run() -> None:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.crypto import blind_index
        from sheaf.models.user import User

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with session() as db:
                result = await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
                user = result.scalar_one()
                user.tier = tier
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_integration_stream_connection_cap(auth_client: httpx.Client):
    # Pin FREE so the cap is finite even on the selfhosted stack (where the
    # default SELF_HOSTED tier is unlimited): hold `cap` open, next is 429.
    _set_tier(auth_client, UserTier.FREE)
    cap = max_front_stream_connections_for_tier(UserTier.FREE)
    key = _create_key(auth_client, ["fronts:read"])
    headers = {"Authorization": f"Bearer {key}"}

    with httpx.Client(base_url=BASE_URL) as stream_client, ExitStack() as stack:
        for _ in range(cap):
            resp = stack.enter_context(
                stream_client.stream(
                    "GET", "/v1/fronts/stream", headers=headers, timeout=15.0
                )
            )
            assert resp.status_code == 200
            # Read the snapshot so the connection is fully established (its slot
            # has been acquired) before opening the next.
            _read_sse_event(resp.iter_lines())

        with httpx.Client(base_url=BASE_URL) as c:
            over = c.get("/v1/fronts/stream", headers=headers, timeout=10.0)
            assert over.status_code == 429


def test_integration_stream_reclaims_dead_slots(auth_client: httpx.Client):
    """A slot leaked by a hard-killed connection is reclaimed on the next
    acquire, independently of any other live connection - so a 24/7 client
    can't wedge the cap with stale slots.

    We inject `cap` already-expired members straight into the account's slot
    set (what hard-killed connections leave behind) and confirm a real
    connection still gets 200 instead of 429: the acquire prunes the dead slots
    first. A single counter could not tell live from dead and would 429.
    """
    import redis as _redis

    _set_tier(auth_client, UserTier.FREE)
    cap = max_front_stream_connections_for_tier(UserTier.FREE)
    user_id = auth_client.get("/v1/auth/me").json()["id"]
    key = _create_key(auth_client, ["fronts:read"])
    headers = {"Authorization": f"Bearer {key}"}

    slots_key = connection_slots_key(str(user_id))
    r = _redis.from_url(
        os.environ.get("SHEAF_TEST_REDIS_URL", "redis://localhost:6380/0")
    )
    try:
        # Fill the cap with members scored in the distant past (epoch second 1),
        # i.e. long expired, as hard-killed connections would leave behind.
        r.zadd(slots_key, {f"dead-{i}": 1.0 for i in range(cap)})
        assert r.zcard(slots_key) == cap  # sanity: cap dead slots present

        with httpx.Client(base_url=BASE_URL) as stream_client, stream_client.stream(
            "GET", "/v1/fronts/stream", headers=headers, timeout=15.0
        ) as resp:
            assert resp.status_code == 200
            assert _read_sse_event(resp.iter_lines())["event"] == "snapshot"
    finally:
        r.delete(slots_key)
        r.close()


@pytest.mark.skipif(
    os.environ.get("SHEAF_TEST_FRONT_STREAM_DISABLED", "false").lower() != "true",
    reason="requires server running with FRONT_STREAM_ENABLED=false",
)
def test_integration_stream_disabled_returns_404(auth_client: httpx.Client):
    key = _create_key(auth_client, ["fronts:read"])
    headers = {"Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=BASE_URL) as c:
        resp = c.get("/v1/fronts/stream", headers=headers, timeout=10.0)
        assert resp.status_code == 404
