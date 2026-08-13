"""Validation for the image-serving security mode."""

import pytest
from pydantic import ValidationError

from sheaf.config import ImageServing, Settings, _validate_settings, settings


@pytest.mark.parametrize("value", ["signed", "unsigned"])
def test_image_serving_accepts_supported_modes(value):
    configured = Settings(_env_file=None, image_serving=value)

    assert configured.image_serving == value
    assert isinstance(configured.image_serving, ImageServing)


def test_image_serving_rejects_typo():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, image_serving="singed")


def _cdn_mode(monkeypatch, **overrides):
    values = {
        "image_serving": ImageServing.SIGNED,
        "storage_backend": "s3",
        "s3_public_url": "https://images.example.com",
        "file_signing_key": "",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)


def test_signed_cdn_mode_without_dedicated_key_refuses_startup(monkeypatch):
    _cdn_mode(monkeypatch)

    with pytest.raises(SystemExit):
        _validate_settings()


@pytest.mark.parametrize(
    "overrides",
    [
        {"file_signing_key": "a" * 64},
        {"s3_public_url": ""},
        {"storage_backend": "filesystem"},
        {"image_serving": ImageServing.UNSIGNED},
    ],
)
def test_signed_cdn_key_check_only_fires_in_cdn_mode(monkeypatch, overrides):
    """Any one leg of the paradigm absent means the key stays optional."""
    _cdn_mode(monkeypatch, **overrides)

    _validate_settings()
