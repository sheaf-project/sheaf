"""Pure-Python coverage for server-side timestamp localisation (Phase 5).

Exercises `sheaf.timezones.localize` and the reminder-digest display string
directly - both run headless (no docker stack, no DB). The deletion-confirmation
email path is covered structurally here via `localize`; its end-to-end body
needs an email-capture backend the test stack doesn't have yet."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sheaf.models.reminder import Reminder, ReminderPending
from sheaf.services.reminders import _digest_payload
from sheaf.timezones import localize


def test_localize_converts_to_named_zone():
    dt = datetime(2026, 7, 12, 13, 30, tzinfo=UTC)  # 13:30 UTC
    ny = localize(dt, "America/New_York")  # July -> EDT (UTC-4) -> 09:30
    assert (ny.year, ny.month, ny.day, ny.hour, ny.minute) == (2026, 7, 12, 9, 30)
    assert ny.strftime("%Z") == "EDT"


def test_localize_none_and_invalid_fall_back_to_utc():
    dt = datetime(2026, 1, 12, 13, 30, tzinfo=UTC)
    for tz in (None, "", "Mars/Nowhere"):
        out = localize(dt, tz)
        assert out.hour == 13
        assert out.strftime("%Z") == "UTC"


def test_localize_respects_dst_transition():
    # Same zone, winter vs summer -> EST vs EDT, resolved from the instant.
    winter = datetime(2026, 1, 12, 17, 0, tzinfo=UTC)
    summer = datetime(2026, 7, 12, 17, 0, tzinfo=UTC)
    assert localize(winter, "America/New_York").strftime("%Z") == "EST"
    assert localize(summer, "America/New_York").strftime("%Z") == "EDT"


def _pending(scheduled_for: datetime) -> ReminderPending:
    row = ReminderPending()
    row.scheduled_for = scheduled_for
    return row


def test_reminder_digest_localises_last_missed_to_schedule_tz():
    reminder = Reminder(title=None, body=None, schedule_tz="America/New_York")
    reminder.id = uuid.uuid4()
    row = _pending(datetime(2026, 7, 12, 13, 30, tzinfo=UTC))
    payload = _digest_payload(reminder, pending_rows=[row])
    # Human-facing string is in the reminder's own schedule zone, stamped.
    assert payload["last_missed_display"] == "2026-07-12 09:30 EDT"
    # The ISO field stays UTC for any structured consumer.
    assert payload["last_missed_at"] == "2026-07-12T13:30:00+00:00"


def test_reminder_digest_display_falls_back_to_utc_without_schedule_tz():
    reminder = Reminder(title=None, body=None, schedule_tz=None)
    reminder.id = uuid.uuid4()
    row = _pending(datetime(2026, 7, 12, 13, 30, tzinfo=UTC))
    payload = _digest_payload(reminder, pending_rows=[row])
    assert payload["last_missed_display"] == "2026-07-12 13:30 UTC"


def test_reminder_digest_display_none_when_no_rows():
    reminder = Reminder(title=None, body=None, schedule_tz="Europe/London")
    reminder.id = uuid.uuid4()
    payload = _digest_payload(reminder, pending_rows=[])
    assert payload["last_missed_display"] is None
    assert payload["last_missed_at"] is None
