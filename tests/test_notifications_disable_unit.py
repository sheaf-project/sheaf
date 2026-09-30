"""The channel switch-off cannot be undone by the owner-notification write.

Host-side, no stack. `_disable_channel` is driven with a fake session that
records the order of commits and savepoints and can be told to fail the
savepoint, the way a flush of an unmigrated enum value failed in production
for days. What is pinned:

* the channel state is committed BEFORE the activity-log write is attempted,
  so a failure there cannot roll the switch-off back;
* that failure is logged and swallowed, not raised, so the caller's own
  commit (the outbox row's terminal state) still happens;
* the happy path writes the activity entry and commits it too.

And for the tick: a row whose processing raised is logged with its traceback
and counted, a cancellation is re-raised, and a clean batch is silent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy.exc import SQLAlchemyError

from sheaf.models.notification_channel import DestinationState, DisabledReason
from sheaf.services.notifications import dispatcher


class _Nested:
    """The async context manager `begin_nested()` returns, optionally failing
    on exit the way a flush inside the savepoint would."""

    def __init__(self, session: _FakeSession, fail: bool):
        self.session = session
        self.fail = fail

    async def __aenter__(self):
        self.session.events.append("savepoint")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.fail:
            self.session.events.append("savepoint-rollback")
            raise SQLAlchemyError("invalid input value for enum activity_action")
        self.session.events.append("savepoint-release")
        return False


@dataclass
class _FakeSession:
    fail_savepoint: bool = False
    events: list[str] = field(default_factory=list)
    added: list[object] = field(default_factory=list)

    def add(self, obj) -> None:
        self.added.append(obj)
        self.events.append("add")

    async def commit(self) -> None:
        self.events.append("commit")

    def begin_nested(self) -> _Nested:
        return _Nested(self, self.fail_savepoint)


@dataclass
class _FakeChannel:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Phone"
    destination_state: str = DestinationState.ACTIVE.value
    disabled_reason: str | None = None
    consecutive_failures: int = 97


@pytest.fixture
def owner(monkeypatch):
    owner_id = uuid.uuid4()

    async def _resolve(db, channel):
        return owner_id, "free"

    monkeypatch.setattr(dispatcher, "_resolve_channel_owner", _resolve)
    return owner_id


def _disable(db: _FakeSession, channel: _FakeChannel) -> None:
    asyncio.run(
        dispatcher._disable_channel(
            db,
            channel,
            reason=DisabledReason.DELIVERY_FAILED,
            detail="404",
            channel_type="ntfy",
        )
    )


def test_switch_off_is_committed_before_the_owner_is_told(owner):
    db = _FakeSession()
    channel = _FakeChannel()

    _disable(db, channel)

    assert channel.destination_state == DestinationState.DISABLED.value
    assert channel.disabled_reason == DisabledReason.DELIVERY_FAILED.value
    # First commit lands the state change; only then the savepoint for the
    # activity entry; then the commit that lands that too.
    assert db.events == ["commit", "savepoint", "add", "savepoint-release", "commit"]
    assert len(db.added) == 1
    assert db.added[0].user_id == owner


def test_a_failed_owner_notification_does_not_undo_the_switch_off(owner, caplog):
    db = _FakeSession(fail_savepoint=True)
    channel = _FakeChannel()

    with caplog.at_level(logging.ERROR, logger="sheaf.notifications.dispatcher"):
        _disable(db, channel)  # must not raise

    assert channel.destination_state == DestinationState.DISABLED.value
    # The state commit happened before the savepoint failed, and nothing
    # after the failure tried to commit the dead entry.
    assert db.events == ["commit", "savepoint", "add", "savepoint-rollback"]
    assert any(
        "activity-log entry could not be written" in r.getMessage()
        for r in caplog.records
    )
    # Logged with the traceback, so the cause is a grep away.
    assert any(r.exc_info for r in caplog.records)


def test_no_owner_means_switch_off_only(monkeypatch):
    async def _resolve(db, channel):
        return None, None

    monkeypatch.setattr(dispatcher, "_resolve_channel_owner", _resolve)
    db = _FakeSession()
    channel = _FakeChannel()

    _disable(db, channel)

    assert channel.destination_state == DestinationState.DISABLED.value
    assert db.events == ["commit"]


# --- the tick's failure report ----------------------------------------------


def test_a_raising_row_is_logged_with_its_traceback(caplog):
    row_id = uuid.uuid4()
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        failure = exc

    with caplog.at_level(logging.ERROR, logger="sheaf.notifications.dispatcher"):
        dispatcher._report_row_failures([row_id, uuid.uuid4()], [failure, None])

    records = [r for r in caplog.records if str(row_id) in r.getMessage()]
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert "boom" in str(records[0].exc_info[1])


def test_a_clean_batch_says_nothing(caplog):
    with caplog.at_level(logging.ERROR, logger="sheaf.notifications.dispatcher"):
        dispatcher._report_row_failures([uuid.uuid4()], [None])
    assert not caplog.records


def test_cancellation_is_not_a_row_failure():
    with pytest.raises(asyncio.CancelledError):
        dispatcher._report_row_failures([uuid.uuid4()], [asyncio.CancelledError()])
