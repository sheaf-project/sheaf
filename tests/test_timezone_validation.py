"""Pure-Python coverage for the display-timezone validation layer.

Runs headless (no docker stack): exercises `is_valid_timezone` and the
`SystemUpdate` schema validator directly. The end-to-end API + export/import
behaviour lives in `test_system_timezone.py` (needs the stack)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sheaf.schemas.system import SystemUpdate
from sheaf.timezones import is_valid_timezone


def test_known_city_zone_is_valid():
    assert is_valid_timezone("America/New_York")


def test_generic_fixed_offset_zones_are_valid():
    # The picker offers these "generic" zones (no city), so validation must
    # accept them too, not just Area/Location city zones.
    assert is_valid_timezone("EST")
    assert is_valid_timezone("EST5EDT")
    assert is_valid_timezone("Etc/GMT+5")
    assert is_valid_timezone("UTC")


def test_unknown_and_junk_zones_are_invalid():
    assert not is_valid_timezone("Mars/Olympus_Mons")
    assert not is_valid_timezone("america/new_york")  # case-sensitive
    assert not is_valid_timezone("")
    assert not is_valid_timezone("auto")  # auto is NULL, not a zone string


def test_schema_accepts_valid_zone():
    body = SystemUpdate(timezone="America/New_York")
    assert body.timezone == "America/New_York"
    assert "timezone" in body.model_fields_set


def test_schema_accepts_null_as_auto():
    # null is a *meaningful* value here (auto), so it must pass validation -
    # unlike the NOT-NULL prefs which reject explicit null.
    body = SystemUpdate(timezone=None)
    assert body.timezone is None
    assert "timezone" in body.model_fields_set


def test_schema_omitted_timezone_is_unset():
    # Omitted -> not in fields_set, so the PATCH handler leaves it unchanged.
    body = SystemUpdate(name="x")
    assert "timezone" not in body.model_fields_set


def test_schema_rejects_unknown_zone():
    with pytest.raises(ValidationError):
        SystemUpdate(timezone="Mars/Olympus_Mons")


def test_schema_rejects_overlong_zone():
    with pytest.raises(ValidationError):
        SystemUpdate(timezone="A" * 65)
