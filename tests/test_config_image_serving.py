"""Validation for the image-serving security mode."""

import pytest
from pydantic import ValidationError

from sheaf.config import ImageServing, Settings


@pytest.mark.parametrize("value", ["signed", "unsigned"])
def test_image_serving_accepts_supported_modes(value):
    configured = Settings(_env_file=None, image_serving=value)

    assert configured.image_serving == value
    assert isinstance(configured.image_serving, ImageServing)


def test_image_serving_rejects_typo():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, image_serving="singed")
