"""Why an account may upload images, as the admin UI now shows it.

Pure unit tests. The admin user table used to show only the per-account
override and label it "Uploads", so on an instance where uploads are on for
everyone, every ordinary account read "No" while uploading happily. These
pin the one-word reasons the server now reports, in the same precedence the
upload endpoint and `animation_allowed` use.
"""

from types import SimpleNamespace

import pytest

from sheaf import image_processing
from sheaf.image_processing import (
    animated_upload_source,
    animation_allowed,
    image_upload_source,
)
from sheaf.models.user import UserTier


def _user(**overrides):
    base = {
        "is_admin": False,
        "can_upload_images": False,
        "can_upload_animated_images": False,
        "tier": UserTier.FREE,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _settings(**overrides):
    base = {"allow_image_uploads": True, "allow_animated_uploads": False}
    base.update(overrides)
    return SimpleNamespace(**base)


# --- images ------------------------------------------------------------------


def test_ordinary_account_on_an_open_instance_is_allowed_by_the_instance():
    """The case that looked like a bug: no override, uploads work anyway."""
    assert image_upload_source(_user(), _settings()) == "instance"


def test_admin_wins_before_the_instance_switch_is_consulted():
    settings = _settings(allow_image_uploads=False)
    assert image_upload_source(_user(is_admin=True), settings) == "admin"


def test_override_only_matters_when_the_instance_switch_is_off():
    assert image_upload_source(_user(can_upload_images=True), _settings()) == "instance"
    assert (
        image_upload_source(_user(can_upload_images=True), _settings(allow_image_uploads=False))
        == "override"
    )


def test_no_switch_no_override_is_off():
    assert image_upload_source(_user(), _settings(allow_image_uploads=False)) == "off"


# --- animated ------------------------------------------------------------------


def test_master_switch_off_beats_everything_for_animated():
    for user in (_user(), _user(is_admin=True), _user(can_upload_animated_images=True)):
        assert animated_upload_source(user, _settings()) == "instance_off"
        assert animation_allowed(user, _settings()) is False


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        (_user(is_admin=True), "admin"),
        (_user(can_upload_animated_images=True), "override"),
        (_user(), "off"),
    ],
)
def test_animated_precedence_with_the_master_switch_on(user, expected):
    settings = _settings(allow_animated_uploads=True)
    assert animated_upload_source(user, settings) == expected
    assert animation_allowed(user, settings) is (expected != "off")


def test_tier_eligibility_is_reported_as_tier(monkeypatch):
    monkeypatch.setattr(image_processing, "ANIMATED_AVATAR_TIERS", {UserTier.PLUS})
    settings = _settings(allow_animated_uploads=True)
    assert animated_upload_source(_user(tier=UserTier.PLUS), settings) == "tier"
    assert animated_upload_source(_user(tier=UserTier.FREE), settings) == "off"
