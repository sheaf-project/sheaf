"""Unit tests for the custom-field definition privacy ceiling.

Pure logic, no DB and no docker stack. What is decidable on the host: the model
columns the staging pair needs, the schemas that carry the level and the step-up
credentials, and the importer's privacy coercer (an untrusted enum string out of
an uploaded file). The end-to-end behaviour - the deferral, the finalize sweep,
the projection refusing a non-public definition, the audit count - lives in
test_field_privacy.py.

The backfill half of the migration is deliberately not covered here: nothing in
this suite drives alembic, so there is no harness to run an upgrade against a
seeded database. Its behaviour is stated in the migration's own docstring.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sheaf.models.custom_field import CustomFieldDefinition
from sheaf.models.system import PrivacyLevel
from sheaf.schemas.custom_field import (
    CustomFieldCreate,
    CustomFieldRead,
    CustomFieldUpdate,
)
from sheaf.services.sheaf_import import _privacy

# --- Model ------------------------------------------------------------------


def test_fields_are_private_by_default():
    """The column has always defaulted private; what changed is that something
    now reads it."""
    assert CustomFieldDefinition.__table__.c.privacy.default.arg == (
        PrivacyLevel.PRIVATE
    )


def test_a_field_can_stage_a_raise():
    """The same pair a group carries, promoted by the same finalize sweep."""
    assert "pending_privacy" in CustomFieldDefinition.__table__.c
    assert "privacy_activates_at" in CustomFieldDefinition.__table__.c
    assert CustomFieldDefinition.__table__.c.pending_privacy.nullable
    assert CustomFieldDefinition.__table__.c.privacy_activates_at.nullable


def test_the_level_lives_on_the_definition_not_the_value():
    """There is deliberately no per-member-per-field level: a setting that says
    "public except for these three" is one the owner has to keep right forever,
    and the day they forget is the day it outs somebody."""
    from sheaf.models.custom_field import CustomFieldValue

    assert "privacy" not in CustomFieldValue.__table__.c


# --- Schemas ----------------------------------------------------------------


def test_field_create_defaults_to_private_and_can_carry_step_up():
    body = CustomFieldCreate(name="F", field_type="text")
    assert body.privacy == PrivacyLevel.PRIVATE
    # Creating a field already public is the same exposure as raising one, so
    # the create body takes the same credentials the update body does.
    assert body.password is None and body.totp_code is None
    assert {"password", "totp_code"} <= set(CustomFieldCreate.model_fields)


def test_field_update_rejects_an_unknown_level():
    with pytest.raises(ValidationError):
        CustomFieldUpdate(privacy="everyone")


def test_field_update_rejects_an_explicit_null_level():
    """Clearing the ceiling is not a thing anyone can ask for."""
    with pytest.raises(ValidationError):
        CustomFieldUpdate(privacy=None)


def test_field_update_carries_step_up_credentials():
    """The credentials ride on the same body as the level, and are absent (not
    empty strings) when they were not supplied - the endpoint pops them before
    anything iterates the update, so they can never reach a column."""
    body = CustomFieldUpdate(privacy="public", password="hunter2")
    assert body.privacy == PrivacyLevel.PUBLIC
    assert body.password == "hunter2"
    assert body.totp_code is None
    assert CustomFieldUpdate().model_dump(exclude_unset=True) == {}


def test_field_read_exposes_the_staging_pair():
    assert {
        "privacy",
        "pending_privacy",
        "privacy_activates_at",
    } <= set(CustomFieldRead.model_fields)


def test_step_up_credentials_are_not_columns():
    """The one invariant that keeps a password out of the database: nothing on
    the model is named after them, so a stray `setattr` loop has nothing to
    land on."""
    columns = set(CustomFieldDefinition.__table__.c.keys())
    assert not ({"password", "totp_code"} & columns)


# --- Importer ---------------------------------------------------------------


@pytest.mark.parametrize("level", ["private", "friends", "public"])
def test_import_accepts_every_level(level: str):
    """A definition this run CREATES takes the file's level as-is: it is in no
    view, so no grant can be serving it, and selecting it into one later is its
    own deliberate act with its own gate. Unlike a group or a member, there is
    nothing to hold back."""
    assert _privacy(level) == level


@pytest.mark.parametrize(
    "garbage",
    [
        None,
        "",
        "PUBLIC",  # case matters; the enum values are lower-case
        "publik",
        "everyone",
        42,
        True,
        pytest.param(b"public", id="bytes-public"),
        ["public"],
        {"visibility": "public"},
    ],
)
def test_import_falls_back_to_private_on_garbage(garbage: object):
    """A garbled file must fail towards "too private", never "published"."""
    assert _privacy(garbage) == PrivacyLevel.PRIVATE
