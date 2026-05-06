"""Unit tests for reminder scheduling primitives.

Covers `compute_next_fire` for daily/weekly/monthly/cron schedules and
`_trigger_matches` for automated front-event matching. These are pure
functions over `Reminder` instances, so we construct them in-memory
without touching the DB.
"""

from datetime import UTC, datetime, timedelta

from sheaf.models.reminder import Reminder
from sheaf.services.reminders import _trigger_matches, compute_next_fire


def _r(**kwargs) -> Reminder:
    """Build a freshly-defaulted Reminder for use in pure-function tests.

    Only fields explicitly supplied are set, plus a created_at default
    (the scheduler uses created_at as the anchor for first-ever fires)."""
    defaults: dict = {
        "id": None,
        "system_id": None,
        "channel_id": None,
        "name": "test",
        "title": "title",
        "trigger_type": "repeated",
        "scope": "system",
        "digest_when_absent": True,
        "enabled": True,
    }
    defaults.update(kwargs)
    r = Reminder(**defaults)
    return r


# --- compute_next_fire: structured schedules ------------------------------


def test_daily_schedule_picks_next_occurrence():
    after = datetime(2026, 5, 6, 8, 0, tzinfo=UTC)
    next_fire = compute_next_fire(
        _r(
            schedule_kind="daily",
            schedule_time="09:00",
            schedule_tz="UTC",
        ),
        after=after,
    )
    assert next_fire == datetime(2026, 5, 6, 9, 0, tzinfo=UTC)


def test_daily_schedule_rolls_to_tomorrow_when_today_passed():
    after = datetime(2026, 5, 6, 10, 0, tzinfo=UTC)
    next_fire = compute_next_fire(
        _r(
            schedule_kind="daily",
            schedule_time="09:00",
            schedule_tz="UTC",
        ),
        after=after,
    )
    assert next_fire == datetime(2026, 5, 7, 9, 0, tzinfo=UTC)


def test_weekly_schedule_picks_next_matching_day():
    """Tue (1, mask bit 2) only schedule starting Mon should land Tuesday."""
    after = datetime(2026, 5, 4, 8, 0, tzinfo=UTC)  # Mon
    next_fire = compute_next_fire(
        _r(
            schedule_kind="weekly",
            schedule_time="09:00",
            schedule_dow_mask=0b0000010,  # Tue only
            schedule_tz="UTC",
        ),
        after=after,
    )
    assert next_fire == datetime(2026, 5, 5, 9, 0, tzinfo=UTC)


def test_weekly_schedule_with_empty_mask_is_inert():
    next_fire = compute_next_fire(
        _r(
            schedule_kind="weekly",
            schedule_time="09:00",
            schedule_dow_mask=0,
            schedule_tz="UTC",
        ),
        after=datetime(2026, 5, 6, tzinfo=UTC),
    )
    assert next_fire is None


def test_monthly_schedule_picks_dom():
    after = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    next_fire = compute_next_fire(
        _r(
            schedule_kind="monthly",
            schedule_time="09:00",
            schedule_dom=15,
            schedule_tz="UTC",
        ),
        after=after,
    )
    assert next_fire == datetime(2026, 5, 15, 9, 0, tzinfo=UTC)


def test_monthly_schedule_skips_when_dom_already_passed():
    after = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    next_fire = compute_next_fire(
        _r(
            schedule_kind="monthly",
            schedule_time="09:00",
            schedule_dom=15,
            schedule_tz="UTC",
        ),
        after=after,
    )
    assert next_fire == datetime(2026, 6, 15, 9, 0, tzinfo=UTC)


def test_schedule_respects_timezone():
    """09:00 NYC = 13:00 UTC (winter EST) / 14:00 UTC (summer EDT)."""
    # Mid-winter so EST is in effect
    after = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    next_fire = compute_next_fire(
        _r(
            schedule_kind="daily",
            schedule_time="09:00",
            schedule_tz="America/New_York",
        ),
        after=after,
    )
    assert next_fire == datetime(2026, 1, 5, 14, 0, tzinfo=UTC)


# --- compute_next_fire: cron mode ----------------------------------------


def test_cron_takes_precedence_over_structured():
    after = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    next_fire = compute_next_fire(
        _r(
            cron_expression="0 9 * * 1",  # Mondays at 09:00
            schedule_kind="daily",  # ignored
            schedule_time="09:00",
            schedule_tz="UTC",
        ),
        after=after,
    )
    # 2026-05-06 is Wed; next Monday is 2026-05-11
    assert next_fire == datetime(2026, 5, 11, 9, 0, tzinfo=UTC)


def test_invalid_cron_yields_none():
    next_fire = compute_next_fire(
        _r(cron_expression="not a cron string", schedule_tz="UTC"),
        after=datetime(2026, 5, 6, tzinfo=UTC),
    )
    assert next_fire is None


# --- compute_next_fire: edge cases ---------------------------------------


def test_automated_reminder_yields_none():
    """Automated reminders are event-driven; they don't have a scheduled
    next-fire."""
    next_fire = compute_next_fire(
        _r(
            trigger_type="automated",
            trigger_event="any",
            delay_seconds=600,
        )
    )
    assert next_fire is None


def test_repeated_with_no_schedule_config_yields_none():
    next_fire = compute_next_fire(_r())  # neither cron nor structured
    assert next_fire is None


def test_unknown_timezone_falls_back_to_utc():
    """Validation rejects bad tz at the API layer; the scheduler tick
    must not crash on data that somehow slipped through."""
    next_fire = compute_next_fire(
        _r(
            schedule_kind="daily",
            schedule_time="09:00",
            schedule_tz="Mars/Olympus",
        ),
        after=datetime(2026, 5, 6, 8, 0, tzinfo=UTC),
    )
    assert next_fire == datetime(2026, 5, 6, 9, 0, tzinfo=UTC)


# --- _trigger_matches ----------------------------------------------------


import uuid  # noqa: E402

ALICE = uuid.uuid4()
BOB = uuid.uuid4()


def test_trigger_matches_specific_member_start():
    r = _r(trigger_type="automated", trigger_event="start", trigger_member_id=ALICE)
    assert _trigger_matches(
        r, started_member_ids={ALICE}, stopped_member_ids=set()
    )
    assert not _trigger_matches(
        r, started_member_ids={BOB}, stopped_member_ids=set()
    )


def test_trigger_matches_any_member_when_member_id_null():
    r = _r(trigger_type="automated", trigger_event="start", trigger_member_id=None)
    assert _trigger_matches(
        r, started_member_ids={BOB}, stopped_member_ids=set()
    )
    assert not _trigger_matches(
        r, started_member_ids=set(), stopped_member_ids={BOB}
    )


def test_trigger_matches_any_event_covers_both_sides():
    r = _r(trigger_type="automated", trigger_event="any", trigger_member_id=ALICE)
    assert _trigger_matches(
        r, started_member_ids={ALICE}, stopped_member_ids=set()
    )
    assert _trigger_matches(
        r, started_member_ids=set(), stopped_member_ids={ALICE}
    )
    assert not _trigger_matches(
        r, started_member_ids={BOB}, stopped_member_ids={BOB}
    )


def test_trigger_matches_stop_event_only():
    r = _r(trigger_type="automated", trigger_event="stop", trigger_member_id=ALICE)
    assert _trigger_matches(
        r, started_member_ids=set(), stopped_member_ids={ALICE}
    )
    assert not _trigger_matches(
        r, started_member_ids={ALICE}, stopped_member_ids=set()
    )


def test_trigger_does_not_match_empty_event_sets():
    r = _r(trigger_type="automated", trigger_event="any", trigger_member_id=None)
    assert not _trigger_matches(
        r, started_member_ids=set(), stopped_member_ids=set()
    )


# --- Daylight Saving sanity checks ---------------------------------------


def test_daily_schedule_around_dst_spring_forward():
    """In NYC on 2026-03-08 the wall clock jumps 02:00 -> 03:00. A daily
    09:00 schedule is well clear of the gap and should fire normally
    that day."""
    after = datetime(2026, 3, 7, 0, 0, tzinfo=UTC)
    next_fire = compute_next_fire(
        _r(
            schedule_kind="daily",
            schedule_time="09:00",
            schedule_tz="America/New_York",
        ),
        after=after,
    )
    assert next_fire is not None
    # 2026-03-07 is in EST (UTC-5), so 09:00 local = 14:00 UTC
    assert next_fire == datetime(2026, 3, 7, 14, 0, tzinfo=UTC)
    # Probe the next day too: 2026-03-08 is in EDT (UTC-4) post-shift
    next_after = compute_next_fire(
        _r(
            schedule_kind="daily",
            schedule_time="09:00",
            schedule_tz="America/New_York",
        ),
        after=next_fire + timedelta(seconds=1),
    )
    assert next_after == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)
