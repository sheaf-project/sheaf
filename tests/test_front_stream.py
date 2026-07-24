"""Tests for the realtime front-change stream (SSE).

Split into:
  - Unit tests (no running stack): the authorized-system-set resolver shape,
    channel/key naming, config defaults, SSE framing, and payload
    serialization. These run under the dummy-URL invocation.
  - Integration tests (need the running app + Redis): function names contain
    "integration" so they can be deselected with `-k "not integration"`. They
    exercise the live endpoint through the shared test stack.
"""

import contextlib
import json
import os
import uuid
from contextlib import ExitStack

import httpx
import pytest

from sheaf.config import Settings
from sheaf.services.front_stream import (
    authorized_front_system_ids,
    build_change_payload,
    build_snapshot_payload,
    connection_count_key,
    format_comment,
    format_sse,
    front_channel,
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


def test_connection_count_key_is_per_account():
    assert connection_count_key("acct-123") == "sheaf:stream:conns:acct-123"


# ---------------------------------------------------------------------------
# Unit: config defaults
# ---------------------------------------------------------------------------

def test_front_stream_config_defaults():
    # Assert the declared defaults, independent of any env override the live
    # test stack might carry.
    fields = Settings.model_fields
    assert fields["front_stream_enabled"].default is True
    assert fields["front_stream_max_connections_per_account"].default == 5
    assert fields["front_stream_heartbeat_seconds"].default == 20
    assert fields["front_stream_auth_recheck_seconds"].default == 60


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
    # event_id present and JSON-serializable
    assert "event_id" in payload
    json.dumps(payload)


def test_build_change_payload_shape_carries_system_id_and_both_states():
    from datetime import UTC, datetime

    sid = uuid.uuid4()
    before_m = uuid.uuid4()
    after_m = uuid.uuid4()
    changed_at = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    event_id = uuid.uuid4()
    payload = build_change_payload(
        sid,
        _state(before_m),
        _state(after_m),
        changed_at=changed_at,
        event_id=event_id,
        emit_ts=123.5,
    )
    assert payload["system_id"] == str(sid)
    assert payload["before"] == [str(before_m)]
    assert payload["after"] == [str(after_m)]
    assert payload["changed_at"] == changed_at.isoformat()
    assert payload["event_id"] == str(event_id)
    assert payload["emit_ts"] == 123.5
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


def test_integration_stream_requires_fronts_read_scope(auth_client: httpx.Client):
    # A key with an unrelated scope must be rejected with 403.
    key = _create_key(auth_client, ["members:read"])
    headers = {"Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=BASE_URL) as c:
        resp = c.get("/v1/fronts/stream", headers=headers, timeout=10.0)
        assert resp.status_code == 403


def test_integration_stream_connection_cap(auth_client: httpx.Client):
    # Default cap is 5; hold that many open, then the next handshake is 429.
    key = _create_key(auth_client, ["fronts:read"])
    headers = {"Authorization": f"Bearer {key}"}
    cap = 5

    with httpx.Client(base_url=BASE_URL) as stream_client, ExitStack() as stack:
        for _ in range(cap):
            resp = stack.enter_context(
                stream_client.stream(
                    "GET", "/v1/fronts/stream", headers=headers, timeout=15.0
                )
            )
            assert resp.status_code == 200
            # Read the snapshot so the connection is fully established (its
            # INCR has landed) before opening the next.
            _read_sse_event(resp.iter_lines())

        with httpx.Client(base_url=BASE_URL) as c:
            over = c.get("/v1/fronts/stream", headers=headers, timeout=10.0)
            assert over.status_code == 429


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
