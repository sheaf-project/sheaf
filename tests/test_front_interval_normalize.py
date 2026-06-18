"""Unit tests for normalize_front_interval (import_content_dedup).

Pure-function tests, no stack needed. The helper is the shared guard every
importer applies before persisting a front so a source export whose end time
precedes its start can't violate the ck_fronts_ended_after_started constraint
and abort the whole import.
"""

from datetime import UTC, datetime

from sheaf.services.import_content_dedup import normalize_front_interval


def _dt(hour: int) -> datetime:
    return datetime(2026, 1, 1, hour, 0, tzinfo=UTC)


def test_swaps_when_end_before_start():
    started, ended, swapped = normalize_front_interval(_dt(10), _dt(9))
    assert swapped is True
    assert started == _dt(9)
    assert ended == _dt(10)


def test_ordered_interval_untouched():
    started, ended, swapped = normalize_front_interval(_dt(9), _dt(10))
    assert swapped is False
    assert (started, ended) == (_dt(9), _dt(10))


def test_equal_endpoints_allowed():
    # ended == started satisfies the >= constraint; no swap.
    started, ended, swapped = normalize_front_interval(_dt(9), _dt(9))
    assert swapped is False
    assert (started, ended) == (_dt(9), _dt(9))


def test_open_front_untouched():
    started, ended, swapped = normalize_front_interval(_dt(9), None)
    assert swapped is False
    assert started == _dt(9)
    assert ended is None
