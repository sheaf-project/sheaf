"""Unit tests for per-purpose upload size resolution."""

import pytest

from sheaf.api.v1.files import _effective_size_limit_mb
from sheaf.config import settings


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 5)
    monkeypatch.setattr(settings, "max_avatar_size_mb", 0)
    monkeypatch.setattr(settings, "max_bio_image_size_mb", 0)


def test_avatar_inherits_fallback_when_unset():
    assert _effective_size_limit_mb("avatar") == 5


def test_bio_inherits_fallback_when_unset():
    assert _effective_size_limit_mb("bio") == 5


def test_avatar_override_applied(monkeypatch):
    monkeypatch.setattr(settings, "max_avatar_size_mb", 2)
    assert _effective_size_limit_mb("avatar") == 2


def test_bio_override_applied(monkeypatch):
    monkeypatch.setattr(settings, "max_bio_image_size_mb", 20)
    assert _effective_size_limit_mb("bio") == 20


def test_avatar_and_bio_are_independent(monkeypatch):
    monkeypatch.setattr(settings, "max_avatar_size_mb", 1)
    monkeypatch.setattr(settings, "max_bio_image_size_mb", 10)
    assert _effective_size_limit_mb("avatar") == 1
    assert _effective_size_limit_mb("bio") == 10


def test_unknown_purpose_falls_through_to_avatar_side():
    """Upload endpoint restricts the 'purpose' query param to avatar|bio, so
    the helper only ever receives those two. But if anything else slips
    through, it should fail closed to the tighter (avatar) side rather than
    silently granting the wider bio limit."""
    assert _effective_size_limit_mb("something_else") == 5