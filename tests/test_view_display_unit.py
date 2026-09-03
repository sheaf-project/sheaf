"""Unit tests for the group privacy ceiling and the per-view display flags.

Pure logic, no DB and no docker stack. What is decidable on the host: the
importer's group-privacy coercer and its hold (an untrusted enum string out of
an uploaded file, plus the decision not to honour it), the schemas that carry
the step-up credentials and the new flags, the staged-flag machinery the two
new exposure flags join, and the fail-closed key sets of the public group
payloads. The end-to-end behaviour (deferral, the finalize sweep, the
projection, the 404 matrices) lives in test_group_privacy.py and
test_public_profiles_api.py.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sheaf.models.group import Group
from sheaf.models.share import ShareView
from sheaf.models.system import PrivacyLevel
from sheaf.schemas.group import GroupCreate, GroupRead, GroupUpdate
from sheaf.schemas.public_profile import (
    PublicGroupMember,
    PublicGroupsView,
    PublicGroupView,
    PublicSystemView,
)
from sheaf.schemas.share import ShareAuditEntry, ShareViewCreate, ShareViewUpdate
from sheaf.services.sharing import EXPOSURE_FLAGS, promote_view_flags
from sheaf.services.sheaf_import import _group_privacy

# --- Importer coercion and hold ---------------------------------------------


@pytest.mark.parametrize("level", ["private", "friends", "public"])
def test_group_privacy_accepts_every_level(level: str):
    got, held = _group_privacy(level, would_show=False)
    assert got == PrivacyLevel(level)
    assert held is False


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
def test_group_privacy_falls_back_to_private_on_garbage(garbage: object):
    """A garbled file must fail towards "too private", never "published"."""
    got, held = _group_privacy(garbage, would_show=True)
    assert got == PrivacyLevel.PRIVATE
    # Nothing was asked for, so nothing was held back and the user is not told
    # about a demotion that did not happen.
    assert held is False


def test_group_privacy_holds_a_public_group_that_would_be_served():
    """The owner-side raise has step-up and a grace window in front of it; an
    import job has neither, so honouring the file would publish a group as a
    side effect of restoring a backup."""
    got, held = _group_privacy("public", would_show=True)
    assert got == PrivacyLevel.PRIVATE
    assert held is True


def test_group_privacy_keeps_public_when_nothing_would_serve_it():
    """The guard must not flatten every import: with no view showing groups,
    nothing would have been served, so the file's level stands."""
    got, held = _group_privacy("public", would_show=False)
    assert got == PrivacyLevel.PUBLIC
    assert held is False


@pytest.mark.parametrize("level", ["private", "friends"])
def test_group_privacy_below_public_is_never_held(level: str):
    """Only `public` is served, so a lower level was never an exposure and must
    not be reported to the user as one."""
    got, held = _group_privacy(level, would_show=True)
    assert got == PrivacyLevel(level)
    assert held is False


# --- Schemas ----------------------------------------------------------------


def test_group_create_defaults_to_private_and_can_carry_step_up():
    body = GroupCreate(name="G")
    assert body.privacy == PrivacyLevel.PRIVATE
    # Creating a group already public is the same exposure as raising one, so
    # the create body takes the same credentials the update body does.
    assert body.password is None and body.totp_code is None
    assert {"password", "totp_code"} <= set(GroupCreate.model_fields)


def test_group_update_rejects_an_unknown_level():
    with pytest.raises(ValidationError):
        GroupUpdate(privacy="everyone")


def test_group_update_carries_step_up_credentials():
    """The credentials ride on the same body as the level, and are absent (not
    empty strings) when they were not supplied - the endpoint pops them before
    anything iterates the update, so they can never reach a column."""
    body = GroupUpdate(privacy="public", password="hunter2")
    assert body.privacy == PrivacyLevel.PUBLIC
    assert body.password == "hunter2"
    assert body.totp_code is None
    assert GroupUpdate().model_dump(exclude_unset=True) == {}


def test_group_read_exposes_the_staging_pair():
    assert {
        "privacy",
        "pending_privacy",
        "privacy_activates_at",
    } <= set(GroupRead.model_fields)


def test_groups_are_private_by_default():
    assert Group.__table__.c.privacy.default.arg == PrivacyLevel.PRIVATE


def test_a_group_can_stage_a_raise():
    """Unlike a group EDGE (which nothing projects and so never stages), a
    group itself is served, so it gets the staging pair."""
    assert "pending_privacy" in Group.__table__.c
    assert "privacy_activates_at" in Group.__table__.c


# --- View display flags -----------------------------------------------------


def test_the_roster_is_on_by_default_and_the_rest_are_off():
    """Every view that existed before these columns did keeps serving exactly
    the roster it served then; a new capability never arrives switched on."""
    assert ShareView.__table__.c.include_members.default.arg is True
    assert ShareView.__table__.c.include_members.server_default.arg == "true"
    assert ShareView.__table__.c.include_groups.default.arg is False
    assert ShareView.__table__.c.member_permalinks.default.arg is False

    body = ShareViewCreate(name="V")
    assert body.include_members is True
    assert body.include_groups is False
    assert body.member_permalinks is False


@pytest.mark.parametrize("flag", ["include_members", "include_groups"])
def test_the_two_new_display_flags_are_staged_exposures(flag: str):
    assert flag in EXPOSURE_FLAGS

    view = ShareView(name="V", **{flag: False})
    setattr(view, f"pending_{flag}", True)
    promote_view_flags(view)
    assert getattr(view, flag) is True
    assert getattr(view, f"pending_{flag}") is None
    assert view.flags_activate_at is None


def test_member_permalinks_is_not_an_exposure_flag():
    """It publishes nothing the roster does not already publish - it only gives
    already-shown members a stable address - so there is no exposure to wait
    out. Staging it would cost a re-auth and a wait for no protection, and
    teach people the grace window is a formality. No pending twin exists, so
    the machinery could not stage it even by accident.
    """
    assert "member_permalinks" not in EXPOSURE_FLAGS
    assert "pending_member_permalinks" not in ShareView.__table__.c
    assert "member_permalinks" in ShareViewUpdate.model_fields


def test_every_exposure_flag_has_a_pending_twin():
    """The one invariant `promote_view_flags` and the finalize UPDATE both
    depend on: a flag in the tuple without a column to stage into would be
    silently un-stageable."""
    for flag in EXPOSURE_FLAGS:
        assert flag in ShareView.__table__.c
        assert f"pending_{flag}" in ShareView.__table__.c


def test_audit_entry_reports_both_new_flags_and_the_group_count():
    assert {
        "include_members",
        "include_groups",
        "member_permalinks",
        "group_count",
    } <= set(ShareAuditEntry.model_fields)


# --- Public payload contracts -----------------------------------------------


def test_public_group_key_sets():
    """Host-side half of the fail-closed contract pinned end-to-end in
    test_public_profiles_api.py."""
    assert set(PublicGroupView.model_fields) == {
        "id", "name", "description", "color", "members",
    }
    # A group member is an id and a name only, like a relationship endpoint:
    # everyone listed is already published in full through /members.
    assert set(PublicGroupMember.model_fields) == {"id", "name"}
    assert set(PublicGroupsView.model_fields) == {"groups"}


def test_member_count_is_nullable():
    """A roster the view refuses to serve must not be countable either, and
    null is the only honest way to say so - zero would be a claim."""
    assert PublicSystemView.model_fields["member_count"].default is None
    body = PublicSystemView(id="i", name="n")
    assert body.member_count is None
